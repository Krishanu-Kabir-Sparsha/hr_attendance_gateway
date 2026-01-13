from odoo import models, fields, _
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class AttendanceProcessor(models.AbstractModel):
    _name = 'attendance.processor'
    _description = 'Attendance Log Processor'

    def process_raw_logs(self, device, raw_logs):
        """Process multiple raw logs with batch optimization"""
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
        device_users_map = self._get_device_users_map(device)

        # Pre-load employees' open attendances
        employee_ids = [du.employee_id.id for du in device_users_map.values() if du.employee_id]
        open_attendances = self._get_open_attendances(employee_ids)

        # Get duplicate threshold (in seconds)
        duplicate_threshold = int(self.env['ir.config_parameter'].sudo().get_param(
            'attendance_gateway.duplicate_threshold', 60
        ))

        raw_logs_to_create = []

        for log_data in raw_logs:
            try:
                device_user_id = str(log_data['device_user_id'])

                # Check for exact duplicate
                existing = self.env['attendance.raw.log'].search([
                    ('device_id', '=', device.id),
                    ('device_user_id', '=', device_user_id),
                    ('timestamp', '=', log_data['timestamp'])
                ], limit=1)

                if existing:
                    result['duplicates'] += 1
                    continue

                # Check for near-duplicate (same user, within threshold seconds)
                timestamp = fields.Datetime.to_datetime(log_data['timestamp'])
                time_start = timestamp - timedelta(seconds=duplicate_threshold)
                time_end = timestamp + timedelta(seconds=duplicate_threshold)

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

                # Store original device punch_type for reference, but we'll override it later
                raw_logs_to_create.append({
                    'device_id': device.id,
                    'device_user_id': device_user_id,
                    'timestamp': log_data['timestamp'],
                    'punch_type': '0',  # Default, will be set correctly during processing
                    'device_punch_type': str(log_data.get('punch_type', '0')),  # Store original
                    'raw_data': str(log_data.get('raw_data', {})),
                    'state': 'pending'
                })

            except Exception as e:
                _logger.error(f"Failed to validate log: {str(e)}")
                result['failed'] += 1

        # Bulk create and process raw logs
        if raw_logs_to_create:
            # Sort by timestamp to process in chronological order
            raw_logs_to_create.sort(key=lambda x: x['timestamp'])
            created_logs = self.env['attendance.raw.log'].create(raw_logs_to_create)

            for raw_log in created_logs: 
                try:
                    device_user = device_users_map.get(raw_log.device_user_id)
                    emp_id = device_user.employee_id.id if device_user and device_user.employee_id else None
                    open_attendance = open_attendances.get(emp_id) if emp_id else None

                    process_result = self.process_single_log(raw_log, device_user, open_attendance)

                    if process_result.get('status') == 'processed':
                        result['processed'] += 1
                        # Update open attendance cache
                        if emp_id:
                            if process_result.get('action') == 'checkin':
                                open_attendances[emp_id] = process_result.get('attendance')
                            elif process_result.get('action') == 'checkout':
                                open_attendances[emp_id] = None
                    elif process_result.get('status') == 'ignored':
                        result['ignored'] += 1
                    else:
                        result['failed'] += 1

                except Exception as e:
                    _logger.error(f"Failed to process log {raw_log.id}: {str(e)}", exc_info=True)
                    raw_log.write({
                        'state': 'error',
                        'error_message': str(e),
                        'processed_date': fields.Datetime.now()
                    })
                    result['failed'] += 1

        return result

    def _get_device_users_map(self, device):
        """Pre-load all device users into a dictionary"""
        device_users = self.env['attendance.device.user'].search([
            ('device_id', '=', device.id),
            ('active', '=', True)
        ])
        return {du.device_user_id: du for du in device_users}

    def _get_open_attendances(self, employee_ids):
        """Pre-load OPEN (no check_out) attendances for employees"""
        if not employee_ids:
            return {}

        open_attendances = {}
        attendances = self.env['hr.attendance'].search([
            ('employee_id', 'in', employee_ids),
            ('check_out', '=', False)
        ])
        for att in attendances:
            open_attendances[att.employee_id.id] = att

        return open_attendances

    def _find_employee_by_badge(self, device, badge_id):
        """Find employee by badge ID with safe field checking"""
        Employee = self.env['hr.employee']
        employee_fields = Employee._fields
        company_id = device.company_id.id if device.company_id else None
        company_domain = [('company_id', 'in', [company_id, False])] if company_id else []

        # Try identification_id first (most common for badges)
        if 'identification_id' in employee_fields:
            employee = Employee.search([
                ('identification_id', '=', badge_id)
            ] + company_domain, limit=1)
            if employee:
                return employee, 'identification_id'

        # Try barcode
        if 'barcode' in employee_fields:
            employee = Employee.search([
                ('barcode', '=', badge_id)
            ] + company_domain, limit=1)
            if employee:
                return employee, 'barcode'

        # Try pin
        if 'pin' in employee_fields:
            employee = Employee.search([
                ('pin', '=', badge_id)
            ] + company_domain, limit=1)
            if employee:
                return employee, 'pin'

        return None, None

    def process_single_log(self, raw_log, device_user=None, open_attendance=None):
        """
        Process a single raw log.
        
        CORE LOGIC:
        - No open attendance = CHECK IN (punch_type = '0')
        - Has open attendance = CHECK OUT (punch_type = '1')
        
        We COMPLETELY IGNORE what the device sends and determine punch_type ourselves! 
        """
        device = raw_log.device_id
        timestamp = raw_log.timestamp

        # Step 1: Get or create device user mapping
        if not device_user:
            device_user = self.env['attendance.device.user'].get_or_create_mapping(
                device, raw_log.device_user_id
            )

        # Step 2: Ensure we have an employee
        if not device_user or not device_user.employee_id:
            employee, match_method = self._find_employee_by_badge(device, raw_log.device_user_id)

            if employee:
                if device_user: 
                    device_user.write({
                        'employee_id': employee.id,
                        'mapping_confidence': 'high',
                        'mapping_method': f'Auto-matched during sync ({match_method})'
                    })
                else:
                    device_user = self.env['attendance.device.user'].create({
                        'device_id': device.id,
                        'device_user_id': raw_log.device_user_id,
                        'employee_id': employee.id,
                        'mapping_confidence': 'high',
                        'mapping_method': f'Auto-created during sync ({match_method})'
                    })
            else:
                raw_log.write({
                    'state': 'error',
                    'error_message': f'No employee found with ID: {raw_log.device_user_id}. '
                                     f'Please set identification_id on employee record.',
                    'processed_date': fields.Datetime.now()
                })
                return {'status': 'error'}

        employee = device_user.employee_id
        raw_log.employee_id = employee.id

        # Step 3: Get open attendance if not provided
        if open_attendance is None:
            open_attendance = self.env['hr.attendance'].search([
                ('employee_id', '=', employee.id),
                ('check_out', '=', False)
            ], order='check_in desc', limit=1)

        # Step 4: Get configuration values
        Shift = self.env['attendance.shift']
        shift = None
        if hasattr(Shift, 'get_employee_shift'):
            shift = Shift.get_employee_shift(employee)

        if shift:
            min_interval_minutes = shift.min_punch_interval
            auto_close_hours = shift.auto_close_hours
        else:
            # Use global settings as fallback
            min_interval_minutes = float(self.env['ir.config_parameter'].sudo().get_param(
                'attendance_gateway.min_punch_interval', 1
            ))
            auto_close_hours = float(self.env['ir.config_parameter'].sudo().get_param(
                'attendance_gateway.auto_close_hours', 20
            ))

        # =====================================================
        # STEP 5: DETERMINE PUNCH TYPE AND ACTION
        # =====================================================

        # CASE A: NO OPEN ATTENDANCE -> This is a CHECK IN
        if not open_attendance:
            _logger.info(f"[CHECK IN] {employee.name} at {timestamp} - No open attendance")
            return self._create_checkin(raw_log, employee, timestamp, shift)

        # CASE B: HAS OPEN ATTENDANCE -> Check time constraints
        time_since_checkin_hours = (timestamp - open_attendance.check_in).total_seconds() / 3600
        time_since_checkin_minutes = time_since_checkin_hours * 60

        _logger.info(
            f"[ANALYZING] {employee.name} - Open check-in from {open_attendance.check_in}, "
            f"Duration: {time_since_checkin_minutes:.1f} min"
        )

        # CASE B1: Too soon after check-in -> IGNORE (don't change punch_type, mark as ignored)
        if time_since_checkin_minutes < min_interval_minutes:
            reason = f'Punch ignored: Only {time_since_checkin_minutes:.1f} min since check-in (minimum: {min_interval_minutes} min)'
            _logger.info(f"[IGNORED] {employee.name} - {reason}")
            raw_log.write({
                'state': 'ignored',
                'error_message': reason,
                'processed_date': fields.Datetime.now()
            })
            return {'status': 'ignored', 'reason': reason}

        # CASE B2: Attendance is STALE -> Auto-close and create new CHECK IN
        if time_since_checkin_hours > auto_close_hours: 
            _logger.info(
                f"[AUTO-CLOSE + CHECK IN] {employee.name} - "
                f"Previous attendance {time_since_checkin_hours:.1f}h old, auto-closing"
            )
            self._auto_close_attendance(open_attendance, shift)
            return self._create_checkin(
                raw_log, employee, timestamp, shift,
                note=f"Previous attendance auto-closed (was {time_since_checkin_hours:.1f}h old)"
            )

        # CASE B3: Valid -> This is a CHECK OUT
        _logger.info(f"[CHECK OUT] {employee.name} at {timestamp} - Duration: {time_since_checkin_hours:.2f}h")
        return self._create_checkout(raw_log, employee, timestamp, open_attendance, shift)

    def _create_checkin(self, raw_log, employee, timestamp, shift=None, note=''):
        """Create a CHECK IN attendance record and set punch_type to '0'"""
        attendance_vals = {
            'employee_id': employee.id,
            'check_in': timestamp,
            'device_id': raw_log.device_id.id,
            'raw_log_id': raw_log.id,
            'is_from_device': True,
        }

        if shift:
            attendance_vals['shift_id'] = shift.id

        if note:
            attendance_vals['note'] = note

        attendance = self.env['hr.attendance'].create(attendance_vals)

        # UPDATE punch_type to CHECK IN ('0')
        raw_log.write({
            'state': 'processed',
            'punch_type': '0',  # CHECK IN
            'attendance_id': attendance.id,
            'error_message': False,
            'processed_date': fields.Datetime.now()
        })

        _logger.info(f"✅ CHECK-IN created for {employee.name} at {timestamp}")

        return {'status': 'processed', 'action': 'checkin', 'attendance': attendance}

    def _create_checkout(self, raw_log, employee, timestamp, attendance, shift=None, note=''):
        """Create a CHECK OUT on existing attendance and set punch_type to '1'"""
        work_hours = (timestamp - attendance.check_in).total_seconds() / 3600

        update_vals = {
            'check_out': timestamp,
        }

        if raw_log.device_id:
            update_vals['device_id'] = raw_log.device_id.id

        # Handle notes
        existing_note = attendance.note or ''
        if note:
            update_vals['note'] = f"{existing_note}\n{note}".strip() if existing_note else note

        # Calculate late/early/overtime status if shift exists
        if shift:
            self._calculate_attendance_status(attendance, timestamp, shift, update_vals)

        attendance.write(update_vals)

        # UPDATE punch_type to CHECK OUT ('1')
        raw_log.write({
            'state': 'processed',
            'punch_type': '1',  # CHECK OUT
            'attendance_id': attendance.id,
            'error_message': False,
            'processed_date': fields.Datetime.now()
        })

        _logger.info(f"✅ CHECK-OUT created for {employee.name} at {timestamp}. Duration: {work_hours:.2f}h")

        return {'status': 'processed', 'action': 'checkout', 'attendance': attendance}

    def _auto_close_attendance(self, attendance, shift=None):
        """Auto-close a stale attendance record"""
        if shift and shift.max_work_hours:
            max_hours = shift.max_work_hours
        else:
            max_hours = float(self.env['ir.config_parameter'].sudo().get_param(
                'attendance_gateway.max_work_duration', 8
            ))

        checkout_time = attendance.check_in + timedelta(hours=max_hours)

        attendance.write({
            'check_out': checkout_time,
            'attendance_status': 'auto_closed',
            'note': f"{attendance.note or ''}\n⚠️ Auto-closed: Missing check-out. Set to {max_hours}h after check-in.".strip()
        })

        _logger.warning(
            f"⚠️ Auto-closed attendance for {attendance.employee_id.name}."
            f"Check-in: {attendance.check_in}, Auto check-out: {checkout_time}"
        )

    def _calculate_attendance_status(self, attendance, checkout_time, shift, update_vals):
        """Calculate attendance status based on shift rules"""
        try:
            timezone = attendance.device_id.timezone if attendance.device_id else 'UTC'
            shift_times = shift.get_shift_times_for_date(attendance.check_in.date(), timezone)

            # Check late check-in
            if attendance.check_in > shift_times['late_checkin_until']:
                update_vals['attendance_status'] = 'late'
                late_seconds = (attendance.check_in - shift_times['shift_start']).total_seconds()
                update_vals['late_minutes'] = max(0, int(late_seconds / 60))

            # Check early leave
            elif checkout_time < shift_times['early_checkout_from']:
                update_vals['attendance_status'] = 'early_leave'
                early_seconds = (shift_times['shift_end'] - checkout_time).total_seconds()
                update_vals['early_leave_minutes'] = max(0, int(early_seconds / 60))

            # Check overtime
            elif shift.overtime_enabled:
                work_hours = (checkout_time - attendance.check_in).total_seconds() / 3600
                if work_hours > shift.overtime_threshold:
                    update_vals['attendance_status'] = 'overtime'
                    update_vals['overtime_hours'] = work_hours - shift.overtime_threshold

        except Exception as e:
            _logger.warning(f"Could not calculate attendance status: {str(e)}")