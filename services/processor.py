from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class AttendanceProcessor(models.AbstractModel):
    _name = 'attendance.processor'
    _description = 'Attendance Punch Processor'

    # ===========================================
    # MAIN PROCESSING METHOD
    # ===========================================
    
    def process_raw_logs(self, device, raw_logs):
        """Process multiple raw logs from device sync."""
        result = {
            'fetched': len(raw_logs),
            'processed': 0,
            'failed': 0,
            'duplicates': 0,
            'ignored': 0
        }

        if not raw_logs:
            return result

        device_users = self._get_device_users_map(device)

        dup_threshold = int(self.env['ir.config_parameter'].sudo().get_param(
            'attendance_gateway.duplicate_threshold', 60
        ))

        try:
            raw_logs = sorted(raw_logs, key=lambda x: x.get('timestamp', ''))
        except Exception as e: 
            _logger.warning(f"Could not sort logs: {e}")

        for log_data in raw_logs:
            try:
                device_user_id = str(log_data.get('device_user_id', '')).strip()
                if not device_user_id:
                    result['failed'] += 1
                    continue

                timestamp = log_data.get('timestamp')
                if not timestamp:
                    result['failed'] += 1
                    continue

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

                device_punch_type = str(log_data.get('punch_type', '0'))

                raw_log = self.env['attendance.raw.log'].create({
                    'device_id': device.id,
                    'device_user_id': device_user_id,
                    'timestamp': timestamp,
                    'punch_type': device_punch_type,
                    'raw_data': str(log_data.get('raw_data', {})),
                    'state': 'pending'
                })

                device_user = device_users.get(device_user_id)
                process_result = self._process_punch(raw_log, device_user, device)

                if process_result.get('success'):
                    result['processed'] += 1
                    if process_result.get('device_user') and device_user_id not in device_users:
                        device_users[device_user_id] = process_result['device_user']
                elif process_result.get('ignored'):
                    result['ignored'] += 1
                else:
                    result['failed'] += 1

            except Exception as e:
                _logger.error(f"Failed to process log: {e}", exc_info=True)
                result['failed'] += 1

        return result

    def process_single_log(self, raw_log, device_user=None):
        """Public method to reprocess a single log"""
        return self._process_punch(raw_log, device_user, raw_log.device_id)

    # ===========================================
    # CORE PROCESSING LOGIC
    # ===========================================

    def _process_punch(self, raw_log, device_user, device):
        """
        Process a single punch with two modes:
        1.SIMPLE MODE (toggle): No slots, just check-in/check-out toggle
        2.SLOT MODE: Time-based punch type determination
        
        Both modes support auto-close of stale attendances.
        """
        result = {'success': False, 'ignored': False, 'device_user': None, 'attendance': None}
        timestamp = raw_log.timestamp

        try:
            # STEP 1: Find/Create Employee Mapping
            if not device_user:
                device_user = self.env['attendance.device.user'].get_or_create_mapping(
                    device, raw_log.device_user_id
                )
                result['device_user'] = device_user

            if not device_user or not device_user.employee_id:
                employee = self._find_employee_by_badge(device, raw_log.device_user_id)
                
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
                    result['device_user'] = device_user
                else:
                    raw_log.write({
                        'state': 'error',
                        'message': f'No employee found for ID: {raw_log.device_user_id}'
                    })
                    return result

            employee = device_user.employee_id
            raw_log.write({'employee_id': employee.id})

            # STEP 2: Get Shift Configuration
            shift = self.env['attendance.shift'].get_employee_shift(employee)
            
            min_gap = shift.min_punch_gap_minutes if shift else 1.0
            auto_close_hours = shift.auto_checkout_after_hours if shift else 16.0
            timezone = device.timezone or 'UTC'

            # STEP 3: Check for stale attendance and auto-close if needed
            # This runs BEFORE processing the current punch
            self._auto_close_stale_attendance(employee, timestamp, auto_close_hours)

            # STEP 4: Determine processing mode
            if shift and shift.use_punch_slots:
                return self._process_with_slots(
                    raw_log, employee, timestamp, shift, device,
                    min_gap, auto_close_hours, timezone
                )
            else:
                return self._process_simple_toggle(
                    raw_log, employee, timestamp, shift, device,
                    min_gap, auto_close_hours
                )

        except Exception as e:
            _logger.error(f"Error processing punch {raw_log.id}: {e}", exc_info=True)
            raw_log.write({
                'state': 'error',
                'message': str(e)[:200]
            })
            return result

    # ===========================================
    # AUTO-CLOSE LOGIC (Works for both modes)
    # ===========================================

    def _auto_close_stale_attendance(self, employee, current_timestamp, auto_close_hours):
        """
        Check for and auto-close any stale open attendance.
        This runs before processing any punch to ensure clean state.
        
        A stale attendance is one where:
        - check_out is empty (still open)
        - Time since check_in exceeds auto_close_hours
        
        Returns: True if an attendance was auto-closed, False otherwise
        """
        open_attendance = self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        if not open_attendance:
            return False

        hours_since = (current_timestamp - open_attendance.check_in).total_seconds() / 3600

        if hours_since > auto_close_hours:
            # Calculate close time (check_in + auto_close_hours)
            close_time = open_attendance.check_in + timedelta(hours=auto_close_hours)
            
            # Don't let close_time be in the future
            if close_time > current_timestamp:
                close_time = current_timestamp - timedelta(minutes=1)

            open_attendance.write({
                'check_out': close_time,
                'note': f"{open_attendance.note or ''}\n⚠️ Auto-closed: No checkout after {auto_close_hours}h".strip()
            })
            
            # Trigger status recalculation
            open_attendance._compute_status()

            _logger.info(
                f"⚠️ AUTO-CLOSED stale attendance for {employee.name}. "
                f"Check-in: {open_attendance.check_in}, Auto check-out: {close_time}"
            )
            return True

        return False

    # ===========================================
    # SIMPLE MODE (Toggle Logic)
    # ===========================================

    def _process_simple_toggle(self, raw_log, employee, timestamp, shift, device,
                                min_gap, auto_close_hours):
        """
        Simple toggle mode: 
        - No open attendance → CHECK IN
        - Has open attendance → CHECK OUT
        
        Note: Stale attendances are already auto-closed before this runs.
        """
        result = {'success': False, 'ignored': False}

        open_attendance = self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        # CASE A: NO OPEN ATTENDANCE → CHECK IN
        if not open_attendance:
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

            _logger.info(f"✓ CHECK IN: {employee.name} at {timestamp}")
            result['success'] = True
            result['attendance'] = attendance
            return result

        # CASE B: HAS OPEN ATTENDANCE
        hours_since = (timestamp - open_attendance.check_in).total_seconds() / 3600
        minutes_since = hours_since * 60

        # B1: Too soon → IGNORE
        if minutes_since < min_gap:
            raw_log.write({
                'state': 'ignored',
                'punch_type': '0',
                'message': f'Ignored: Only {minutes_since:.1f} min since check-in (min: {min_gap} min)'
            })
            result['ignored'] = True
            return result

        # B2: Normal → CHECK OUT (stale case already handled by _auto_close_stale_attendance)
        open_attendance.write({'check_out': timestamp})
        open_attendance._compute_status()

        raw_log.write({
            'state': 'processed',
            'punch_type': '1',
            'attendance_id': open_attendance.id,
            'message': f'Check-out ({hours_since:.2f}h worked)'
        })

        _logger.info(f"✓ CHECK OUT: {employee.name} ({hours_since:.2f}h)")
        result['success'] = True
        result['attendance'] = open_attendance
        return result

    # ===========================================
    # SLOT MODE (Time-Based Punch Type)
    # ===========================================

    def _process_with_slots(self, raw_log, employee, timestamp, shift, device,
                            min_gap, auto_close_hours, timezone):
        """
        Slot mode: Punch type is determined by time window.
        
        Note: Stale attendances are already auto-closed before this runs,
        so we always have a clean state to work with.
        """
        result = {'success': False, 'ignored': False}

        open_attendance = self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False)
        ], order='check_in desc', limit=1)

        # Determine punch type from slot
        slot_punch_type = self._get_slot_punch_type(shift, timestamp, timezone)
        
        if slot_punch_type is None:
            raw_log.write({
                'state': 'ignored',
                'message': f'No matching time slot for punch at {timestamp.strftime("%H:%M")}'
            })
            _logger.info(f"⊘ IGNORED (no slot): {employee.name} at {timestamp}")
            result['ignored'] = True
            return result

        raw_log.write({'punch_type': slot_punch_type})
        _logger.info(f"Slot determined punch type '{slot_punch_type}' for {employee.name} at {timestamp}")

        # Process based on punch type
        if slot_punch_type == '0': 
            return self._slot_check_in(raw_log, employee, timestamp, shift, device, open_attendance)
        elif slot_punch_type == '1':
            return self._slot_check_out(raw_log, employee, timestamp, shift, device, open_attendance, min_gap)
        elif slot_punch_type == '2':
            return self._slot_break_out(raw_log, employee, timestamp, open_attendance)
        elif slot_punch_type == '3': 
            return self._slot_break_in(raw_log, employee, timestamp, open_attendance)
        elif slot_punch_type == '4': 
            return self._slot_overtime_start(raw_log, employee, timestamp, open_attendance)
        elif slot_punch_type == '5': 
            return self._slot_overtime_end(raw_log, employee, timestamp, open_attendance)

        raw_log.write({
            'state': 'error',
            'message': f'Unknown punch type: {slot_punch_type}'
        })
        return result

    def _get_slot_punch_type(self, shift, timestamp, timezone):
        """Get punch type from matching slot"""
        if not shift.punch_slot_ids:
            return None

        for slot in shift.punch_slot_ids.filtered(lambda s: s.active).sorted('sequence'):
            if slot.is_time_in_window(timestamp, timezone):
                return slot.punch_type

        return None

    # ===========================================
    # SLOT MODE: Individual Punch Handlers
    # ===========================================

    def _slot_check_in(self, raw_log, employee, timestamp, shift, device, open_attendance):
        """Handle Check In in slot mode"""
        result = {'success': False, 'ignored': False}

        # If there's still an open attendance (shouldn't happen after auto-close, but just in case)
        if open_attendance:
            raw_log.write({
                'state': 'ignored',
                'message': f'Already checked in at {open_attendance.check_in.strftime("%H:%M")}.Use Check Out slot.'
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
            'attendance_id': attendance.id,
            'message': 'Check-in created'
        })

        _logger.info(f"✓ CHECK IN (slot): {employee.name} at {timestamp}")
        result['success'] = True
        result['attendance'] = attendance
        return result

    def _slot_check_out(self, raw_log, employee, timestamp, shift, device, open_attendance, min_gap):
        """Handle Check Out in slot mode"""
        result = {'success': False, 'ignored': False}

        if not open_attendance:
            raw_log.write({
                'state': 'ignored',
                'message': 'Check Out ignored: No active check-in. Please check in first.'
            })
            result['ignored'] = True
            return result

        hours_since = (timestamp - open_attendance.check_in).total_seconds() / 3600
        minutes_since = hours_since * 60

        if minutes_since < min_gap: 
            raw_log.write({
                'state': 'ignored',
                'message': f'Ignored: Only {minutes_since:.1f} min since check-in'
            })
            result['ignored'] = True
            return result

        open_attendance.write({'check_out': timestamp})
        open_attendance._compute_status()

        raw_log.write({
            'state': 'processed',
            'attendance_id': open_attendance.id,
            'message': f'Check-out ({hours_since:.2f}h worked)'
        })

        _logger.info(f"✓ CHECK OUT (slot): {employee.name} ({hours_since:.2f}h)")
        result['success'] = True
        result['attendance'] = open_attendance
        return result

    def _slot_break_out(self, raw_log, employee, timestamp, open_attendance):
        """Handle Break Out"""
        result = {'success': False, 'ignored': False}

        if not open_attendance: 
            raw_log.write({
                'state': 'ignored',
                'message': 'Break Out ignored: No active check-in'
            })
            result['ignored'] = True
            return result

        note = open_attendance.note or ''
        
        # Check if already on break (has Break Out without matching Break In)
        break_outs = note.count('Break Out: ')
        break_ins = note.count('Break In:')
        if break_outs > break_ins:
            raw_log.write({
                'state': 'ignored',
                'message': 'Break Out ignored: Already on break. Please clock back in first.'
            })
            result['ignored'] = True
            return result

        break_note = f"Break Out: {timestamp.strftime('%H:%M')}"
        open_attendance.write({
            'note': f"{note}\n{break_note}".strip()
        })

        raw_log.write({
            'state': 'processed',
            'attendance_id': open_attendance.id,
            'message': 'Break started'
        })

        _logger.info(f"✓ BREAK OUT: {employee.name} at {timestamp}")
        result['success'] = True
        result['attendance'] = open_attendance
        return result

    def _slot_break_in(self, raw_log, employee, timestamp, open_attendance):
        """Handle Break In"""
        result = {'success': False, 'ignored': False}

        if not open_attendance:
            raw_log.write({
                'state': 'ignored',
                'message': 'Break In ignored: No active check-in'
            })
            result['ignored'] = True
            return result

        note = open_attendance.note or ''
        
        # Check sequence: must have more Break Outs than Break Ins
        break_outs = note.count('Break Out:')
        break_ins = note.count('Break In:')
        if break_outs <= break_ins:
            raw_log.write({
                'state': 'ignored',
                'message': 'Break In ignored: Not on break.Please clock out for break first.'
            })
            result['ignored'] = True
            return result

        # Calculate break duration
        break_duration = 0
        try:
            # Find the last Break Out time
            import re
            break_out_times = re.findall(r'Break Out: (\d{2}:\d{2})', note)
            if break_out_times:
                last_break_out = break_out_times[-1]
                from datetime import datetime
                break_out_time = datetime.strptime(last_break_out, '%H:%M').time()
                break_in_time = timestamp.time()
                
                break_out_minutes = break_out_time.hour * 60 + break_out_time.minute
                break_in_minutes = break_in_time.hour * 60 + break_in_time.minute
                break_duration = break_in_minutes - break_out_minutes
                
                if break_duration < 0: # Crossed midnight
                    break_duration += 24 * 60
        except Exception as e:
            _logger.warning(f"Could not calculate break duration: {e}")

        break_note = f" | Break In: {timestamp.strftime('%H:%M')}"
        if break_duration > 0:
            break_note += f" ({break_duration} min)"
            current_break = open_attendance.break_minutes or 0
            open_attendance.write({
                'break_minutes': current_break + break_duration
            })

        open_attendance.write({
            'note': f"{note}{break_note}".strip()
        })

        raw_log.write({
            'state': 'processed',
            'attendance_id': open_attendance.id,
            'message': f'Break ended ({break_duration} min)' if break_duration else 'Break ended'
        })

        _logger.info(f"✓ BREAK IN: {employee.name} at {timestamp} ({break_duration} min)")
        result['success'] = True
        result['attendance'] = open_attendance
        return result

    def _slot_overtime_start(self, raw_log, employee, timestamp, open_attendance):
        """Handle Overtime Start"""
        result = {'success': False, 'ignored': False}

        if not open_attendance:
            raw_log.write({
                'state': 'ignored',
                'message': 'Overtime Start ignored: No active check-in'
            })
            result['ignored'] = True
            return result

        note = open_attendance.note or ''
        
        # Check if already in overtime
        ot_starts = note.count('OT Start:')
        ot_ends = note.count('OT End:')
        if ot_starts > ot_ends:
            raw_log.write({
                'state': 'ignored',
                'message': 'Overtime Start ignored: Already in overtime session.'
            })
            result['ignored'] = True
            return result

        ot_note = f"OT Start: {timestamp.strftime('%H:%M')}"
        open_attendance.write({
            'note': f"{note}\n{ot_note}".strip()
        })

        raw_log.write({
            'state': 'processed',
            'attendance_id': open_attendance.id,
            'message': 'Overtime started'
        })

        _logger.info(f"✓ OVERTIME START: {employee.name} at {timestamp}")
        result['success'] = True
        result['attendance'] = open_attendance
        return result

    def _slot_overtime_end(self, raw_log, employee, timestamp, open_attendance):
        """Handle Overtime End"""
        result = {'success': False, 'ignored': False}

        if not open_attendance:
            raw_log.write({
                'state': 'ignored',
                'message': 'Overtime End ignored: No active check-in'
            })
            result['ignored'] = True
            return result

        note = open_attendance.note or ''
        
        # Check sequence
        ot_starts = note.count('OT Start:')
        ot_ends = note.count('OT End:')
        if ot_starts <= ot_ends:
            raw_log.write({
                'state': 'ignored',
                'message': 'Overtime End ignored: No overtime started.'
            })
            result['ignored'] = True
            return result

        ot_note = f" | OT End: {timestamp.strftime('%H:%M')}"
        open_attendance.write({
            'note': f"{note}{ot_note}".strip()
        })

        raw_log.write({
            'state': 'processed',
            'attendance_id': open_attendance.id,
            'message': 'Overtime ended'
        })

        _logger.info(f"✓ OVERTIME END: {employee.name} at {timestamp}")
        result['success'] = True
        result['attendance'] = open_attendance
        return result

    # ===========================================
    # HELPER METHODS
    # ===========================================

    def _get_device_users_map(self, device):
        """Pre-load device users into a dict for fast lookup"""
        device_users = self.env['attendance.device.user'].search([
            ('device_id', '=', device.id),
            ('active', '=', True)
        ])
        return {du.device_user_id: du for du in device_users}

    def _find_employee_by_badge(self, device, badge_id):
        """Find employee by badge ID across multiple fields"""
        Employee = self.env['hr.employee']
        employee_fields = Employee._fields
        
        company_domain = []
        if device.company_id:
            company_domain = [('company_id', 'in', [device.company_id.id, False])]

        for field_name in ['identification_id', 'barcode', 'pin']: 
            if field_name in employee_fields:
                employee = Employee.search(
                    [(field_name, '=', badge_id)] + company_domain,
                    limit=1
                )
                if employee:
                    return employee

        return None

    # ===========================================
    # SCHEDULED AUTO-CLOSE (Called by Cron)
    # ===========================================

    @api.model
    def cron_auto_close_stale_attendances(self):
        """
        Scheduled job to auto-close all stale attendances.
        This handles cases where employees forget to check out
        and don't punch the next day.
        """
        # Get default auto-close hours from settings
        default_auto_close = float(self.env['ir.config_parameter'].sudo().get_param(
            'attendance_gateway.auto_close_hours', 16.0
        ))

        # Find all open attendances
        open_attendances = self.env['hr.attendance'].search([
            ('check_out', '=', False)
        ])

        closed_count = 0
        now = fields.Datetime.now()

        for attendance in open_attendances:
            # Get employee's shift for auto_close_hours
            shift = self.env['attendance.shift'].get_employee_shift(attendance.employee_id)
            auto_close_hours = shift.auto_checkout_after_hours if shift else default_auto_close

            hours_since = (now - attendance.check_in).total_seconds() / 3600

            if hours_since > auto_close_hours:
                close_time = attendance.check_in + timedelta(hours=auto_close_hours)
                
                attendance.write({
                    'check_out': close_time,
                    'note': f"{attendance.note or ''}\n⚠️ Auto-closed by system: No checkout after {auto_close_hours}h".strip()
                })
                attendance._compute_status()
                
                _logger.info(f"Cron auto-closed attendance for {attendance.employee_id.name}")
                closed_count += 1

        if closed_count: 
            _logger.info(f"Cron job auto-closed {closed_count} stale attendances")

        return closed_count