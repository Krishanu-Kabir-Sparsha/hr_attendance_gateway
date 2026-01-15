from odoo import models, fields, api, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class AttendanceProcessor(models.AbstractModel):
    _name = 'attendance.processor'
    _description = 'Attendance Processor'

    def process_raw_logs(self, device, raw_logs):
        """Process multiple raw logs from device"""
        result = {
            'fetched': len(raw_logs),
            'processed': 0,
            'failed': 0,
            'duplicates': 0,
            'ignored': 0
        }

        if not raw_logs: 
            return result

        # Pre-load device users
        device_users = {}
        for du in self.env['attendance.device.user'].search([
            ('device_id', '=', device.id),
            ('active', '=', True)
        ]):
            device_users[du.device_user_id] = du

        # Get duplicate threshold
        dup_threshold = int(self.env['ir.config_parameter'].sudo().get_param(
            'attendance_gateway.duplicate_threshold', 60
        ))

        # Sort by timestamp for chronological processing
        try:
            raw_logs = sorted(raw_logs, key=lambda x: x.get('timestamp', ''))
        except Exception as e:
            _logger.warning(f"Could not sort logs: {e}")

        for log_data in raw_logs:
            try: 
                device_user_id = str(log_data.get('device_user_id', ''))
                if not device_user_id:
                    result['failed'] += 1
                    continue

                timestamp = log_data.get('timestamp')
                if not timestamp:
                    result['failed'] += 1
                    continue

                # Convert timestamp
                if isinstance(timestamp, str):
                    timestamp = fields.Datetime.to_datetime(timestamp)

                # Check for exact duplicate
                existing = self.env['attendance.raw.log'].search([
                    ('device_id', '=', device.id),
                    ('device_user_id', '=', device_user_id),
                    ('timestamp', '=', timestamp)
                ], limit=1)

                if existing: 
                    result['duplicates'] += 1
                    continue

                # Check for near-duplicate
                time_start = timestamp - timedelta(seconds=dup_threshold)
                time_end = timestamp + timedelta(seconds=dup_threshold)

                near_duplicate = self.env['attendance.raw.log'].search([
                    ('device_id', '=', device.id),
                    ('device_user_id', '=', device_user_id),
                    ('timestamp', '>=', time_start),
                    ('timestamp', '<=', time_end),
                    ('state', 'in', ['processed', 'pending'])
                ], limit=1)

                if near_duplicate:
                    result['duplicates'] += 1
                    continue

                # Get punch type from device (important!)
                device_punch_type = str(log_data.get('punch_type', '0'))

                # Create raw log
                raw_log = self.env['attendance.raw.log'].create({
                    'device_id': device.id,
                    'device_user_id': device_user_id,
                    'timestamp': timestamp,
                    'punch_type': device_punch_type,
                    'raw_data': str(log_data.get('raw_data', {})),
                    'state': 'pending'
                })

                # Process the log
                device_user = device_users.get(device_user_id)
                process_result = self._process_single_log(raw_log, device_user, device)

                if process_result.get('success'):
                    result['processed'] += 1
                elif process_result.get('ignored'):
                    result['ignored'] += 1
                else: 
                    result['failed'] += 1

            except Exception as e:
                _logger.error(f"Failed to process log: {e}", exc_info=True)
                result['failed'] += 1

        return result

    def process_single_log(self, raw_log, device_user=None):
        """Public method to process a single log"""
        return self._process_single_log(raw_log, device_user, raw_log.device_id)

    def _process_single_log(self, raw_log, device_user, device):
        """Internal method to process a single punch log"""
        timestamp = raw_log.timestamp
        result = {'success': False, 'ignored': False}

        try:
            # ===========================================
            # STEP 1: Find or Create Employee Mapping
            # ===========================================
            if not device_user: 
                device_user = self.env['attendance.device.user'].get_or_create_mapping(
                    device, raw_log.device_user_id
                )

            if not device_user or not device_user.employee_id:
                # Try to find employee by badge
                employee = self._find_employee(device, raw_log.device_user_id)
                if employee:
                    if device_user: 
                        device_user.write({
                            'employee_id': employee.id,
                            'mapping_confidence': 'high',
                            'mapping_method': 'Auto-matched during sync'
                        })
                    else:
                        device_user = self.env['attendance.device.user'].create({
                            'device_id': device.id,
                            'device_user_id': raw_log.device_user_id,
                            'employee_id': employee.id,
                            'mapping_confidence': 'high',
                            'mapping_method': 'Auto-created during sync'
                        })
                else:
                    raw_log.write({
                        'state': 'error',
                        'message': f'No employee found for ID: {raw_log.device_user_id}'
                    })
                    return result

            employee = device_user.employee_id
            raw_log.write({'employee_id': employee.id})

            # ===========================================
            # STEP 2: Get Shift Configuration
            # ===========================================
            shift = self.env['attendance.shift'].get_employee_shift(employee)

            min_gap = shift.min_punch_gap_minutes if shift else 1.0
            auto_close = shift.auto_checkout_after_hours if shift else 16.0

            # ===========================================
            # STEP 3: Determine Final Punch Type
            # ===========================================
            # Priority: Slot-based > Device-reported > Auto-detect
            punch_type = raw_log.punch_type or '0'

            if shift and shift.use_punch_slots:
                timezone = device.timezone or 'UTC'
                for slot in shift.punch_slot_ids.filtered(lambda s: s.active).sorted('sequence'):
                    if slot.is_time_in_window(timestamp, timezone):
                        punch_type = slot.punch_type
                        break

            # ===========================================
            # STEP 4: Find Open Attendance
            # ===========================================
            open_attendance = self.env['hr.attendance'].search([
                ('employee_id', '=', employee.id),
                ('check_out', '=', False)
            ], order='check_in desc', limit=1)

            # ===========================================
            # STEP 5: Process Based on Punch Type & State
            # ===========================================

            # BREAK PUNCHES (2, 3) - Just record in notes
            if punch_type in ['2', '3']:
                return self._handle_break_punch(raw_log, employee, punch_type, timestamp, open_attendance)

            # OVERTIME PUNCHES (4, 5) - Just record in notes
            if punch_type in ['4', '5']:
                return self._handle_overtime_punch(raw_log, employee, punch_type, timestamp, open_attendance, shift)

            # REGULAR CHECK IN/OUT (0, 1)
            # CASE A: No open attendance
            if not open_attendance: 
                if punch_type == '1':
                    # Device says checkout but no open attendance
                    raw_log.write({
                        'state': 'ignored',
                        'punch_type': '1',
                        'message': 'Check-out ignored: No open check-in found'
                    })
                    result['ignored'] = True
                    return result

                # Create new check-in
                attendance = self.env['hr.attendance'].create({
                    'employee_id': employee.id,
                    'check_in': timestamp,
                    'device_id': device.id,
                    'shift_id': shift.id if shift else False,
                    'is_from_device': True,
                })
                raw_log.write({
                    'state': 'processed',
                    'punch_type': '0',
                    'attendance_id': attendance.id,
                    'message': 'Check-in created'
                })
                _logger.info(f"CHECK IN: {employee.name} at {timestamp}")
                result['success'] = True
                return result

            # CASE B: Has open attendance
            hours_since = (timestamp - open_attendance.check_in).total_seconds() / 3600
            minutes_since = hours_since * 60

            # B1: Too soon - Ignore (duplicate prevention)
            if minutes_since < min_gap:
                raw_log.write({
                    'state': 'ignored',
                    'message': f'Ignored: Only {minutes_since:.1f} min since check-in (min gap: {min_gap} min)'
                })
                result['ignored'] = True
                return result

            # B2: Very old attendance - Auto-close and create new check-in
            if hours_since > auto_close:
                # Auto-close the old attendance
                close_time = open_attendance.check_in + timedelta(hours=auto_close)
                open_attendance.write({
                    'check_out': close_time,
                    'note': f"{open_attendance.note or ''}\n⚠️ Auto-closed after {auto_close}h (missing check-out)".strip()
                })
                # Force status update
                open_attendance._compute_status()

                # Create new check-in
                attendance = self.env['hr.attendance'].create({
                    'employee_id': employee.id,
                    'check_in': timestamp,
                    'device_id': device.id,
                    'shift_id': shift.id if shift else False,
                    'is_from_device': True,
                    'note': 'Previous attendance was auto-closed'
                })
                raw_log.write({
                    'state': 'processed',
                    'punch_type': '0',
                    'attendance_id': attendance.id,
                    'message': f'Check-in created (previous auto-closed after {auto_close}h)'
                })
                _logger.info(f"AUTO-CLOSE + CHECK IN: {employee.name}")
                result['success'] = True
                return result

            # B3: Normal check-out
            open_attendance.write({
                'check_out': timestamp,
            })
            raw_log.write({
                'state': 'processed',
                'punch_type': '1',
                'attendance_id': open_attendance.id,
                'message': f'Check-out created ({hours_since:.2f}h worked)'
            })
            _logger.info(f"CHECK OUT: {employee.name} at {timestamp} ({hours_since:.2f}h)")
            result['success'] = True
            return result

        except Exception as e: 
            _logger.error(f"Error processing log {raw_log.id}: {e}", exc_info=True)
            raw_log.write({
                'state': 'error',
                'message': str(e)[:200]
            })
            return result

    def _handle_break_punch(self, raw_log, employee, punch_type, timestamp, open_attendance):
        """Handle break start/end punches"""
        result = {'success': False, 'ignored': False}

        break_type = 'Break Out' if punch_type == '2' else 'Break In'

        if not open_attendance: 
            raw_log.write({
                'state': 'ignored',
                'message': f'{break_type} ignored: No active check-in'
            })
            result['ignored'] = True
            return result

        # Add to attendance notes
        note = f"{break_type}: {timestamp.strftime('%H:%M')}"
        current_note = open_attendance.note or ''
        open_attendance.write({
            'note': f"{current_note}\n{note}".strip()
        })

        raw_log.write({
            'state': 'processed',
            'punch_type': punch_type,
            'attendance_id': open_attendance.id,
            'message': f'{break_type} recorded'
        })

        _logger.info(f"{break_type}: {employee.name} at {timestamp}")
        result['success'] = True
        return result

    def _handle_overtime_punch(self, raw_log, employee, punch_type, timestamp, open_attendance, shift):
        """Handle overtime start/end punches"""
        result = {'success': False, 'ignored': False}

        ot_type = 'Overtime Start' if punch_type == '4' else 'Overtime End'

        if open_attendance:
            # Add to current attendance notes
            note = f"{ot_type}: {timestamp.strftime('%H:%M')}"
            current_note = open_attendance.note or ''
            open_attendance.write({
                'note': f"{current_note}\n{note}".strip()
            })

            raw_log.write({
                'state': 'processed',
                'punch_type': punch_type,
                'attendance_id': open_attendance.id,
                'message': f'{ot_type} recorded'
            })
        else:
            # No open attendance - create overtime check-in
            if punch_type == '4':
                attendance = self.env['hr.attendance'].create({
                    'employee_id': employee.id,
                    'check_in': timestamp,
                    'device_id': raw_log.device_id.id,
                    'shift_id': shift.id if shift else False,
                    'is_from_device': True,
                    'note': 'Overtime session'
                })
                raw_log.write({
                    'state': 'processed',
                    'punch_type': punch_type,
                    'attendance_id': attendance.id,
                    'message': 'Overtime check-in created'
                })
            else:
                raw_log.write({
                    'state': 'ignored',
                    'message': 'Overtime End ignored: No active session'
                })
                result['ignored'] = True
                return result

        _logger.info(f"{ot_type}: {employee.name} at {timestamp}")
        result['success'] = True
        return result

    def _find_employee(self, device, badge_id):
        """Find employee by badge ID"""
        Employee = self.env['hr.employee']
        company_domain = []
        if device.company_id: 
            company_domain = [('company_id', 'in', [device.company_id.id, False])]

        # Try different fields
        for field in ['identification_id', 'barcode', 'pin']: 
            if field in Employee._fields:
                emp = Employee.search([(field, '=', badge_id)] + company_domain, limit=1)
                if emp: 
                    return emp
        return None