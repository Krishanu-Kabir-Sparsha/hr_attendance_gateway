from odoo import models, fields, api, _
from odoo.exceptions import UserError

class SyncAttendanceWizard(models.TransientModel):
    _name = 'sync.attendance.wizard'
    _description = 'Sync Attendance Wizard'
    
    device_id = fields.Many2one('attendance.device', string='Device', required=True)
    from_date = fields.Datetime(string='From Date')
    to_date = fields.Datetime(string='To Date')
    
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'device_id' in fields_list and self.env.context.get('default_device_id'):
            res['device_id'] = self.env.context['default_device_id']
        if 'from_date' in fields_list and not res.get('from_date'):
            device = self.env['attendance.device'].browse(res.get('device_id'))
            if device.last_sync_date:
                res['from_date'] = device.last_sync_date
        return res
    
    def action_sync(self):
        self.ensure_one()
        
        try:
            self.device_id._sync_attendance_logs()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Attendance synced successfully'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            raise UserError(_("Sync failed: %s") % str(e))