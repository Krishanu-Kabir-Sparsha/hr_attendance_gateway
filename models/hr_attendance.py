from odoo import models, fields, api


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    device_id = fields.Many2one(
        'attendance.device',
        string='Source Device',
        readonly=True
    )
    raw_log_id = fields.Many2one(
        'attendance.raw.log',
        string='Raw Log',
        readonly=True
    )
    is_from_device = fields.Boolean(
        string='From Device',
        default=False,
        readonly=True
    )
    note = fields.Text(
        string='Notes',
        help='Additional information like breaks, overtime, auto-close reason, etc.'
    )

    # Shift reference
    shift_id = fields.Many2one(
        'attendance.shift',
        string='Shift',
        help='The shift applicable for this attendance record'
    )

    # Attendance status
    attendance_status = fields.Selection([
        ('normal', 'Normal'),
        ('late', 'Late Check-in'),
        ('early_leave', 'Early Leave'),
        ('overtime', 'Overtime'),
        ('auto_closed', 'Auto Closed'),
    ], string='Status', default='normal')

    late_minutes = fields.Integer(string='Late Minutes', default=0)
    early_leave_minutes = fields.Integer(string='Early Leave Minutes', default=0)
    overtime_hours = fields.Float(string='Overtime Hours', default=0.0)

    @api.model
    def create(self, vals):
        """Override to calculate attendance status"""
        record = super().create(vals)
        record._compute_attendance_status()
        return record

    def write(self, vals):
        """Override to recalculate attendance status on checkout"""
        result = super().write(vals)
        if 'check_out' in vals: 
            for record in self:
                record._compute_attendance_status()
        return result

    def _compute_attendance_status(self):
        """Calculate attendance status based on shift rules"""
        for record in self:
            if not record.shift_id or not record.check_in:
                continue

            shift = record.shift_id
            
            # Get device timezone or default to UTC
            timezone = 'UTC'
            if record.device_id and record.device_id.timezone:
                timezone = record.device_id.timezone

            shift_times = shift.get_shift_times_for_date(record.check_in.date(), timezone)

            # Check late check-in
            if record.check_in > shift_times['late_checkin_until']:
                record.attendance_status = 'late'
                late_seconds = (record.check_in - shift_times['shift_start']).total_seconds()
                record.late_minutes = max(0, int(late_seconds / 60))

            # Check early leave and overtime (only if checked out)
            if record.check_out:
                work_hours = (record.check_out - record.check_in).total_seconds() / 3600

                if record.check_out < shift_times['early_checkout_from']:
                    record.attendance_status = 'early_leave'
                    early_seconds = (shift_times['shift_end'] - record.check_out).total_seconds()
                    record.early_leave_minutes = max(0, int(early_seconds / 60))
                elif shift.overtime_enabled and work_hours > shift.overtime_threshold:
                    record.attendance_status = 'overtime'
                    record.overtime_hours = work_hours - shift.overtime_threshold
                elif record.attendance_status not in ['late', 'auto_closed']:
                    record.attendance_status = 'normal'