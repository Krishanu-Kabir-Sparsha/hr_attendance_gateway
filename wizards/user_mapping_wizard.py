from odoo import models, fields, api, _

class UserMappingWizard(models.TransientModel):
    _name = 'user.mapping.wizard'
    _description = 'User Mapping Wizard'
    
    device_id = fields.Many2one('attendance.device', string='Device', required=True)
    line_ids = fields.One2many('user.mapping.wizard.line', 'wizard_id', string='Mappings')
    
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('default_device_id'):
            device_id = self.env.context['default_device_id']
            res['device_id'] = device_id
        return res
    
    def action_fetch_users(self):
        self.ensure_one()
        self.line_ids.unlink()
        
        device_users = self.env['attendance.device.user'].search([
            ('device_id', '=', self.device_id.id),
            ('employee_id', '=', False)
        ])
        
        lines = []
        for du in device_users:
            lines.append((0, 0, {
                'device_user_id': du.id,
                'device_user_code': du.device_user_id,
                'device_user_name': du.device_user_name
            }))
        
        self.line_ids = lines
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new'
        }
    
    def action_apply_mappings(self):
        for line in self.line_ids.filtered(lambda l: l.employee_id):
            line.device_user_id.employee_id = line.employee_id.id

class UserMappingWizardLine(models.TransientModel):
    _name = 'user.mapping.wizard.line'
    _description = 'User Mapping Line'
    
    wizard_id = fields.Many2one('user.mapping.wizard', required=True, ondelete='cascade')
    device_user_id = fields.Many2one('attendance.device.user', string='Device User', required=True)
    device_user_code = fields.Char(string='User Code', readonly=True)
    device_user_name = fields.Char(string='User Name', readonly=True)
    employee_id = fields.Many2one('hr.employee', string='Map to Employee')