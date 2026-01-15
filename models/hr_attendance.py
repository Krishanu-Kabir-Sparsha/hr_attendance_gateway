from odoo import models, fields, api, _


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    # Source Tracking
    device_id = fields.Many2one('attendance.device', string='Device', readonly=True)
    is_from_device = fields.Boolean(string='From Device', default=False, readonly=True)
    shift_id = fields.Many2one('attendance.shift', string='Shift')

    # Status
    status = fields.Selection([
        ('checked_in', 'Checked In'),
        ('on_time', 'On Time'),
        ('late', 'Late'),
        ('early_leave', 'Early Leave'),
        ('half_day', 'Half Day'),
        ('overtime', 'Overtime'),
        ('auto_closed', 'Auto Closed'),
    ], string='Status', compute='_compute_status', store=True)

    # Time Tracking
    late_minutes = fields.Integer(string='Late (min)', compute='_compute_status', store=True)
    early_leave_minutes = fields.Integer(string='Early Leave (min)', compute='_compute_status', store=True)
    overtime_minutes = fields.Integer(string='Overtime (min)', compute='_compute_status', store=True)
    break_minutes = fields.Integer(string='Break (min)', default=0)

    # Notes
    note = fields.Text(string='Notes')

    @api.depends('check_in', 'check_out', 'shift_id', 'employee_id')
    def _compute_status(self):
        for record in self: 
            # Reset
            record.late_minutes = 0
            record.early_leave_minutes = 0
            record.overtime_minutes = 0

            if not record.check_in:
                record.status = 'checked_in'
                continue

            # No checkout yet
            if not record.check_out:
                record.status = 'checked_in'
                continue

            # Get shift
            shift = record.shift_id
            if not shift: 
                shift = self.env['attendance.shift'].get_employee_shift(record.employee_id)

            if not shift:
                record.status = 'on_time'
                continue

            # Get timezone
            timezone = 'UTC'
            if record.device_id and record.device_id.timezone:
                timezone = record.device_id.timezone

            try:
                boundaries = shift.get_shift_boundaries(record.check_in.date(), timezone)
            except Exception: 
                record.status = 'on_time'
                continue

            # Check if auto-closed (keep that status)
            if record.note and 'Auto-closed' in record.note:
                record.status = 'auto_closed'
                continue

            # Calculate late minutes
            if record.check_in > boundaries['late_threshold']: 
                diff = (record.check_in - boundaries['shift_start']).total_seconds() / 60
                record.late_minutes = int(max(0, diff))

            # Calculate early leave
            if record.check_out < boundaries['early_leave_threshold']:
                diff = (boundaries['shift_end'] - record.check_out).total_seconds() / 60
                record.early_leave_minutes = int(max(0, diff))

            # Calculate overtime
            worked_hours = record.worked_hours or 0
            if worked_hours > shift.overtime_after_hours:
                record.overtime_minutes = int((worked_hours - shift.overtime_after_hours) * 60)

            # Determine status (priority order)
            if worked_hours < shift.half_day_hours: 
                record.status = 'half_day'
            elif record.overtime_minutes > 0:
                record.status = 'overtime'
            elif record.late_minutes > 0:
                record.status = 'late'
            elif record.early_leave_minutes > 0:
                record.status = 'early_leave'
            else:
                record.status = 'on_time'

    def action_recalculate_status(self):
        """Manually recalculate status"""
        self._compute_status()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Status recalculated'),
                'type': 'success',
            }
        }