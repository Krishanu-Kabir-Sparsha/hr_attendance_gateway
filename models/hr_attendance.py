from odoo import models, fields, api, _
from datetime import timedelta

import logging

_logger = logging.getLogger(__name__)

class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    # ===========================================
    # SOURCE TRACKING
    # ===========================================
    device_id = fields.Many2one(
        'attendance.device',
        string='Device',
        readonly=True
    )
    is_from_device = fields.Boolean(
        string='From Device',
        default=False,
        readonly=True
    )
    shift_id = fields.Many2one(
        'attendance.shift',
        string='Shift'
    )

    # ===========================================
    # ATTENDANCE STATUS
    # ===========================================
    status = fields.Selection([
        ('checked_in', 'Checked In'),      # Currently working (no checkout)
        ('on_time', 'On Time'),            # Normal attendance
        ('late', 'Late'),                  # Arrived after grace period
        ('early_leave', 'Early Leave'),   # Left before grace period
        ('half_day', 'Half Day'),          # Worked less than threshold
        ('overtime', 'Overtime'),          # Worked more than threshold
        ('auto_closed', 'Auto Closed'),    # System closed (missing checkout)
    ], string='Status', compute='_compute_status', store=True)

    # ===========================================
    # TIME TRACKING
    # ===========================================
    late_minutes = fields.Integer(
        string='Late (min)',
        compute='_compute_status',
        store=True
    )
    early_leave_minutes = fields.Integer(
        string='Early Leave (min)',
        compute='_compute_status',
        store=True
    )
    overtime_minutes = fields.Integer(
        string='Overtime (min)',
        compute='_compute_status',
        store=True
    )
    break_minutes = fields.Integer(
        string='Break (min)',
        default=0
    )

    # ===========================================
    # NOTES
    # ===========================================
    note = fields.Text(string='Notes')

    @api.depends('check_in', 'check_out', 'shift_id', 'employee_id')
    def _compute_status(self):
        """
        Calculate attendance status based on shift rules.
        
        Status priority (when checked out):
        1.auto_closed - If was auto-closed by system
        2.half_day - If worked less than threshold
        3.overtime - If worked more than threshold
        4.late - If arrived late
        5.early_leave - If left early
        6.on_time - Normal attendance
        
        If not checked out: checked_in
        """
        for record in self:
            # Reset computed values
            record.late_minutes = 0
            record.early_leave_minutes = 0
            record.overtime_minutes = 0
            
            # No check-in means no status
            if not record.check_in:
                record.status = False
                continue

            # Still working (no checkout)
            if not record.check_out:
                record.status = 'checked_in'
                continue

            # Check if already marked as auto_closed
            if record.note and 'Auto-closed' in record.note:
                record.status = 'auto_closed'
                continue

            # Get shift (from record or employee default)
            shift = record.shift_id
            if not shift:
                Shift = self.env['attendance.shift']
                shift = Shift.get_employee_shift(record.employee_id)

            # No shift = just mark as on_time
            if not shift: 
                record.status = 'on_time'
                continue

            # Get timezone
            timezone = 'UTC'
            if record.device_id and record.device_id.timezone:
                timezone = record.device_id.timezone

            # Get shift boundaries for check-in date
            try:
                boundaries = shift.get_shift_boundaries(record.check_in.date(), timezone)
            except Exception as e:
                _logger.warning(f"Could not calculate shift boundaries: {e}")
                record.status = 'on_time'
                continue

            # Calculate worked hours
            worked_hours = record.worked_hours or 0

            # ===========================================
            # CHECK LATE (arrived after late_threshold)
            # ===========================================
            if record.check_in > boundaries['late_threshold']:
                diff_seconds = (record.check_in - boundaries['shift_start']).total_seconds()
                record.late_minutes = int(max(0, diff_seconds / 60))

            # ===========================================
            # CHECK EARLY LEAVE (left before early_leave_threshold)
            # ===========================================
            if record.check_out < boundaries['early_leave_threshold']:
                diff_seconds = (boundaries['shift_end'] - record.check_out).total_seconds()
                record.early_leave_minutes = int(max(0, diff_seconds / 60))

            # ===========================================
            # CHECK OVERTIME
            # ===========================================
            if worked_hours > shift.overtime_after_hours:
                overtime_hours = worked_hours - shift.overtime_after_hours
                record.overtime_minutes = int(overtime_hours * 60)

            # ===========================================
            # DETERMINE FINAL STATUS (priority order)
            # ===========================================
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
        """Manually trigger status recalculation"""
        self._compute_status()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Status Updated'),
                'message': _('Attendance status has been recalculated'),
                'type': 'success',
            }
        }