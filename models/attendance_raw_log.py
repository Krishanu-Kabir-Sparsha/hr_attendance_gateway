from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class AttendanceRawLog(models.Model):
    _name = 'attendance.raw.log'
    _description = 'Raw Attendance Log'
    _order = 'timestamp desc'
    _rec_name = 'device_user_id'

    device_id = fields.Many2one(
        'attendance.device', string='Device',
        required=True, ondelete='cascade', index=True
    )
    device_user_id = fields.Char(
        string='Device User ID',
        required=True, index=True
    )
    timestamp = fields.Datetime(
        string='Timestamp',
        required=True, index=True
    )

    # This is the CORRECT punch type determined by our system logic
    punch_type = fields.Selection([
        ('0', 'Check In'),
        ('1', 'Check Out'),
        ('2', 'Break Start'),
        ('3', 'Break End'),
        ('4', 'Overtime In'),
        ('5', 'Overtime Out'),
    ], string='Punch Type', default='0',
       help='The punch type determined by our system based on attendance logic')

    # This stores what the device originally sent (for debugging/reference only)
    device_punch_type = fields.Char(
        string='Device Original Type',
        help='What the device originally sent (for reference only)'
    )

    raw_data = fields.Text(string='Raw Data')

    state = fields.Selection([
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('error', 'Error'),
        ('duplicate', 'Duplicate'),
        ('ignored', 'Ignored'),
    ], string='Status', default='pending', index=True)

    employee_id = fields.Many2one(
        'hr.employee', string='Employee',
        readonly=True, index=True
    )
    attendance_id = fields.Many2one(
        'hr.attendance', string='Attendance Record',
        readonly=True
    )
    error_message = fields.Text(string='Message')
    processed_date = fields.Datetime(string='Processed Date')

    company_id = fields.Many2one(
        'res.company', string='Company',
        related='device_id.company_id', store=True
    )

    _sql_constraints = [
        ('unique_log', 'UNIQUE(device_id, device_user_id, timestamp)',
         'Duplicate attendance log detected!'),
    ]

    def action_reprocess(self):
        """Reprocess failed/pending/ignored logs"""
        processor = self.env['attendance.processor']

        success_count = 0
        failed_count = 0

        for record in self.filtered(lambda r: r.state in ['error', 'pending', 'ignored']):
            try:
                # Reset state
                record.write({
                    'state': 'pending',
                    'error_message': False,
                    'punch_type': '0'  # Reset to default, will be set correctly
                })

                # Reprocess
                processor.process_single_log(record)
                success_count += 1

            except Exception as e:
                failed_count += 1
                _logger.error(f"Reprocess failed for log {record.id}: {str(e)}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reprocess Complete'),
                'message': _('Success: %d, Failed: %d') % (success_count, failed_count),
                'type': 'success' if failed_count == 0 else 'warning',
            }
        }

    def action_view_attendance(self):
        """Open the related attendance record"""
        self.ensure_one()
        if self.attendance_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Attendance'),
                'res_model': 'hr.attendance',
                'res_id': self.attendance_id.id,
                'view_mode': 'form',
                'target': 'current',
            }