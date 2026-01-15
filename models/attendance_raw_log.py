from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class AttendanceRawLog(models.Model):
    _name = 'attendance.raw.log'
    _description = 'Device Punch Log'
    _order = 'timestamp desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name')

    device_id = fields.Many2one(
        'attendance.device', string='Device',
        required=True, ondelete='cascade', index=True
    )
    device_user_id = fields.Char(string='Device User ID', required=True, index=True)
    timestamp = fields.Datetime(string='Punch Time', required=True, index=True)

    punch_type = fields.Selection([
        ('0', 'Check In'),
        ('1', 'Check Out'),
        ('2', 'Break Out'),
        ('3', 'Break In'),
        ('4', 'Overtime Start'),
        ('5', 'Overtime End'),
    ], string='Punch Type', default='0')

    state = fields.Selection([
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('ignored', 'Ignored'),
        ('error', 'Error'),
    ], string='Status', default='pending', index=True)

    employee_id = fields.Many2one('hr.employee', string='Employee', readonly=True, index=True)
    attendance_id = fields.Many2one('hr.attendance', string='Attendance', readonly=True)
    message = fields.Char(string='Message')
    raw_data = fields.Text(string='Raw Data')

    company_id = fields.Many2one('res.company', related='device_id.company_id', store=True)

    _sql_constraints = [
        ('unique_log', 'UNIQUE(device_id, device_user_id, timestamp)', 'Duplicate punch detected! '),
    ]

    def _compute_display_name(self):
        for record in self: 
            emp_name = record.employee_id.name if record.employee_id else record.device_user_id
            punch_labels = dict(self._fields['punch_type'].selection)
            punch_label = punch_labels.get(record.punch_type, 'Unknown')
            record.display_name = f"{emp_name} - {punch_label} @ {record.timestamp}"

    def action_reprocess(self):
        """Reprocess selected logs"""
        processor = self.env['attendance.processor']
        success = 0
        failed = 0

        for log in self.filtered(lambda l: l.state in ['pending', 'error', 'ignored']):
            try: 
                log.write({'state': 'pending', 'message': False})
                processor.process_single_log(log)
                if log.state == 'processed':
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                log.write({'state': 'error', 'message': str(e)[:200]})
                _logger.error(f"Reprocess failed for log {log.id}: {e}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reprocess Complete'),
                'message': _('Success: %d, Failed: %d') % (success, failed),
                'type': 'success' if failed == 0 else 'warning',
            }
        }

    def action_view_attendance(self):
        """Open related attendance"""
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