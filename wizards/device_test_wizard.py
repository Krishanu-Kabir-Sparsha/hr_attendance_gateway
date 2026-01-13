from odoo import models, fields, _

class DeviceTestWizard(models.TransientModel):
    _name = 'device.test.wizard'
    _description = 'Device Test Wizard'
    
    device_id = fields.Many2one('attendance.device', string='Device', required=True)
    test_result = fields.Text(string='Result', readonly=True)
    
    def action_test(self):
        self.ensure_one()
        result = self.device_id.action_test_connection()
        self.test_result = result