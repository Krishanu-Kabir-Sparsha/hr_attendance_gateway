from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class SyncAttendanceWizard(models.TransientModel):
    _name = 'sync.attendance.wizard'
    _description = 'Sync Attendance Wizard'

    device_id = fields.Many2one('attendance.device', string='Device', required=True)
    from_date = fields.Datetime(string='From Date')
    to_date = fields.Datetime(string='To Date')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('default_device_id'):
            device = self.env['attendance.device'].browse(self.env.context['default_device_id'])
            res['device_id'] = device.id
            if device.last_sync_date: 
                res['from_date'] = device.last_sync_date
        return res

    def action_sync(self):
        self.ensure_one()

        if self.device_id.state != 'active': 
            raise UserError(_("Device must be active to sync"))

        try:
            result = self.device_id._sync_attendance_logs()

            message = _(
                'Sync completed!\n'
                '- Fetched: %d\n'
                '- Processed: %d\n'
                '- Duplicates: %d\n'
                '- Failed: %d'
            ) % (
                result.get('fetched', 0),
                result.get('processed', 0),
                result.get('duplicates', 0),
                result.get('failed', 0)
            )

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Complete'),
                    'message': message,
                    'type': 'success' if result.get('failed', 0) == 0 else 'warning',
                    'sticky': True,
                }
            }
        except Exception as e:
            _logger.error(f"Sync wizard error: {e}", exc_info=True)
            raise UserError(_("Sync failed: %s") % str(e))