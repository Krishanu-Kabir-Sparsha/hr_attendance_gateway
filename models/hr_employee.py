from odoo import models, fields, api


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Device mappings
    device_user_ids = fields.One2many(
        'attendance.device.user',
        'employee_id',
        string='Device Mappings'
    )
    device_user_count = fields.Integer(
        string='Devices',
        compute='_compute_device_user_count'
    )

    # Shift assignment
    shift_id = fields.Many2one(
        'attendance.shift',
        string='Work Shift',
        help='Assigned work shift for this employee. If not set, company default will be used.'
    )

    def _compute_device_user_count(self):
        for employee in self:
            employee.device_user_count = len(employee.device_user_ids)

    def action_view_device_mappings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Device Mappings',
            'res_model': 'attendance.device.user',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id}
        }

    def get_current_shift(self):
        """Get the current applicable shift for this employee"""
        self.ensure_one()
        Shift = self.env['attendance.shift']
        return Shift.get_employee_shift(self)