from odoo import models, fields, api, _
from odoo.exceptions import UserError

class ManualAttendanceWizard(models.TransientModel):
    _name = 'manual.attendance.wizard'
    _description = 'Manual Attendance Override'
    
    raw_log_id = fields.Many2one('attendance.raw.log', string='Raw Log', required=True)
    device_user_id = fields.Char(related='raw_log_id.device_user_id', readonly=True)
    timestamp = fields.Datetime(related='raw_log_id.timestamp', readonly=True)
    
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True)
    action_type = fields.Selection([
        ('checkin', 'Check In'),
        ('checkout', 'Check Out'),
        ('ignore', 'Ignore This Log')
    ], string='Action', required=True, default='checkin')
    
    adjusted_timestamp = fields.Datetime(string='Adjusted Time')
    reason = fields.Text(string='Reason', required=True)
    
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            raw_log = self.env['attendance.raw.log'].browse(self.env.context['active_id'])
            res.update({
                'raw_log_id': raw_log.id,
                'adjusted_timestamp': raw_log.timestamp,
                'employee_id': raw_log.employee_id.id if raw_log.employee_id else False
            })
        return res
    
    def action_apply(self):
        self.ensure_one()
        
        if self.action_type == 'ignore':
            self.raw_log_id.write({
                'state': 'ignored',
                'error_message': f"Manually ignored: {self.reason}",
                'processed_date': fields.Datetime.now()
            })
            return
        
        # Create or update attendance
        timestamp = self.adjusted_timestamp or self.raw_log_id.timestamp
        
        if self.action_type == 'checkin':
            attendance = self.env['hr.attendance'].create({
                'employee_id': self.employee_id.id,
                'check_in': timestamp,
                'device_id': self.raw_log_id.device_id.id,
                'raw_log_id': self.raw_log_id.id,
                'is_from_device': True,
                'note': f"Manual override: {self.reason}"
            })
        else:  # checkout
            last_attendance = self.env['hr.attendance'].search([
                ('employee_id', '=', self.employee_id.id),
                ('check_out', '=', False)
            ], order='check_in desc', limit=1)
            
            if not last_attendance:
                raise UserError(_("No open check-in found for this employee"))
            
            attendance = last_attendance
            attendance.write({
                'check_out': timestamp,
                'note': f"{attendance.note or ''}\nManual check-out: {self.reason}".strip()
            })
        
        self.raw_log_id.write({
            'state': 'processed',
            'attendance_id': attendance.id,
            'employee_id': self.employee_id.id,
            'error_message': f"Manual override applied: {self.reason}",
            'processed_date': fields.Datetime.now()
        })
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Manual attendance override applied'),
                'type': 'success',
            }
        }