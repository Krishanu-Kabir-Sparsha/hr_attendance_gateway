from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging
import re

_logger = logging.getLogger(__name__)


class AttendanceDevice(models.Model):
    _name = 'attendance.device'
    _description = 'Attendance Device'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    name = fields.Char(string='Device Name', required=True, tracking=True)
    code = fields.Char(string='Device Code', required=True, copy=False, tracking=True)

    device_type = fields.Selection([
        ('zkteco', 'ZKTeco Device'),
        ('hikvision', 'Hikvision Access Control'),
        ('suprema', 'Suprema BioStar'),
        ('api_rest', 'Generic REST API'),
        ('api_soap', 'Generic SOAP API'),
        ('webhook', 'Webhook Push'),
        ('custom', 'Custom Protocol'),
    ], string='Device Type', required=True, tracking=True)

    connection_type = fields.Selection([
        ('tcp', 'TCP/IP'),
        ('http', 'HTTP/HTTPS'),
        ('webhook', 'Webhook'),
    ], string='Connection Type', default='tcp')

    # Connection Details
    ip_address = fields.Char(string='IP Address')
    port = fields.Integer(string='Port', default=4370)
    api_url = fields.Char(string='API URL')
    api_key = fields.Char(string='API Key')
    api_secret = fields.Char(string='API Secret')
    username = fields.Char(string='Username')
    password = fields.Char(string='Password')

    # Webhook
    webhook_token = fields.Char(string='Webhook Token', readonly=True, copy=False)
    webhook_url = fields.Char(string='Webhook URL', compute='_compute_webhook_url')

    # Sync Configuration
    sync_mode = fields.Selection([
        ('pull', 'Pull from Device'),
        ('push', 'Device Push to Server'),
        ('both', 'Bidirectional'),
    ], string='Sync Mode', default='pull', required=True)

    auto_sync = fields.Boolean(string='Auto Sync', default=True)
    sync_interval = fields.Integer(string='Sync Interval (minutes)', default=15)
    last_sync_date = fields.Datetime(string='Last Sync', readonly=True)

    # Status
    active = fields.Boolean(string='Active', default=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('error', 'Error'),
    ], string='Status', default='draft', tracking=True)

    is_online = fields.Boolean(string='Online', compute='_compute_is_online')

    # Timezone
    timezone = fields.Selection(selection='_get_timezones', string='Device Timezone', default='UTC', required=True)

    # Statistics
    total_users = fields.Integer(string='Total Users', compute='_compute_statistics')
    total_logs = fields.Integer(string='Total Logs', compute='_compute_statistics')
    last_log_date = fields.Datetime(string='Last Log Date', readonly=True)

    # Relations
    raw_log_ids = fields.One2many('attendance.raw.log', 'device_id', string='Raw Logs')
    sync_log_ids = fields.One2many('attendance.sync.log', 'device_id', string='Sync Logs')
    device_user_ids = fields.One2many('attendance.device.user', 'device_id', string='Device Users')

    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    _sql_constraints = [
        ('code_unique', 'UNIQUE(code, company_id)', 'Device code must be unique per company!'),
    ]

    @api.model
    def _get_timezones(self):
        import pytz
        return [(tz, tz) for tz in pytz.all_timezones]

    @api.model
    def create(self, vals):
        if vals.get('device_type') == 'webhook' and not vals.get('webhook_token'):
            vals['webhook_token'] = self._generate_webhook_token()
        return super().create(vals)

    def _generate_webhook_token(self):
        import secrets
        return secrets.token_urlsafe(32)

    @api.depends('webhook_token')
    def _compute_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self: 
            if record.webhook_token:
                record.webhook_url = f"{base_url}/attendance/webhook/{record.webhook_token}"
            else:
                record.webhook_url = False

    def _compute_is_online(self):
        for record in self: 
            record.is_online = False
            if record.state == 'active':
                try:
                    adapter = record._get_adapter()
                    record.is_online = adapter.test_connection()
                except Exception: 
                    pass

    def _compute_statistics(self):
        for record in self: 
            record.total_users = len(record.device_user_ids)
            record.total_logs = self.env['attendance.raw.log'].search_count([('device_id', '=', record.id)])

    def _get_adapter(self):
        """Get device adapter"""
        adapter_map = {
            'zkteco': 'ZKTecoAdapter',
            'api_rest': 'RestAPIAdapter',
            'webhook': 'WebhookAdapter',
        }

        adapter_class_name = adapter_map.get(self.device_type)
        if not adapter_class_name: 
            raise UserError(_("Unsupported device type: %s") % self.device_type)

        from odoo.addons.hr_attendance_gateway import adapters
        adapter_class = getattr(adapters, adapter_class_name, None)

        if not adapter_class:
            raise UserError(_("Adapter class %s not found") % adapter_class_name)

        return adapter_class(self)

    def action_test_connection(self):
        """Test device connection"""
        self.ensure_one()
        try:
            adapter = self._get_adapter()
            if adapter.test_connection():
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Connection successful! '),
                        'type': 'success',
                    }
                }
            else:
                raise UserError(_("Connection test failed"))
        except Exception as e: 
            raise UserError(_("Connection failed: %s") % str(e))

    def action_sync_now(self):
        """Manual sync trigger"""
        self.ensure_one()
        if self.state != 'active':
            raise UserError(_("Device must be active to sync"))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync Attendance'),
            'res_model': 'sync.attendance.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_device_id': self.id}
        }

    def action_fetch_users(self):
        """Fetch users from device"""
        self.ensure_one()
        try:
            adapter = self._get_adapter()
            users = adapter.get_users()

            created = 0
            updated = 0

            for user_data in users: 
                device_user_id = str(user_data.get('device_user_id', ''))
                if not device_user_id:
                    continue

                # Try to extract badge from name
                device_user_name = user_data.get('name', '')
                extracted = None
                if device_user_name:
                    numbers = re.findall(r'\d{6,}', device_user_name)
                    if numbers:
                        extracted = numbers[0]

                final_id = extracted or device_user_id

                existing = self.env['attendance.device.user'].search([
                    ('device_id', '=', self.id),
                    ('device_user_id', '=', final_id)
                ], limit=1)

                # Try auto-match
                employee = self._find_employee_by_badge(final_id)

                vals = {
                    'device_id': self.id,
                    'device_user_id': final_id,
                    'device_user_name': device_user_name,
                    'card_number': user_data.get('card_number') or '',
                }

                if employee:
                    vals.update({
                        'employee_id': employee.id,
                        'mapping_confidence': 'high',
                        'mapping_method': 'Auto-matched during fetch'
                    })

                if not existing:
                    self.env['attendance.device.user'].create(vals)
                    created += 1
                elif employee and not existing.employee_id:
                    existing.write({
                        'employee_id': employee.id,
                        'mapping_confidence': 'high',
                        'mapping_method': 'Auto-matched during fetch'
                    })
                    updated += 1

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Fetched %d users. Created: %d, Updated: %d') % (len(users), created, updated),
                    'type': 'success',
                }
            }
        except Exception as e: 
            raise UserError(_("Failed to fetch users: %s") % str(e))

    def _find_employee_by_badge(self, badge_id):
        """Find employee by badge ID"""
        Employee = self.env['hr.employee']
        company_domain = [('company_id', 'in', [self.company_id.id, False])] if self.company_id else []

        for field in ['identification_id', 'barcode', 'pin']:
            if field in Employee._fields:
                emp = Employee.search([(field, '=', badge_id)] + company_domain, limit=1)
                if emp: 
                    return emp
        return None

    def action_activate(self):
        self.write({'state': 'active'})

    def action_deactivate(self):
        self.write({'state': 'inactive'})

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Punch Logs'),
            'res_model': 'attendance.raw.log',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id}
        }

    def action_view_sync_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync History'),
            'res_model': 'attendance.sync.log',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
        }

    @api.model
    def cron_sync_attendance(self):
        """Cron job to sync all active devices"""
        devices = self.search([
            ('state', '=', 'active'),
            ('auto_sync', '=', True),
            ('sync_mode', 'in', ['pull', 'both'])
        ])

        for device in devices: 
            try:
                device._sync_attendance_logs()
            except Exception as e:
                _logger.error(f"Sync failed for {device.name}: {e}")
                device.message_post(body=_("Sync failed: %s") % str(e))

    def _sync_attendance_logs(self):
        """Core sync logic"""
        self.ensure_one()

        sync_log = self.env['attendance.sync.log'].create({
            'device_id': self.id,
            'sync_date': fields.Datetime.now(),
            'state': 'in_progress'
        })

        try:
            adapter = self._get_adapter()
            from_date = self.last_sync_date or (fields.Datetime.now() - timedelta(days=7))
            raw_logs = adapter.get_attendance_logs(from_date=from_date)

            processor = self.env['attendance.processor']
            result = processor.process_raw_logs(self, raw_logs)

            sync_log.write({
                'state': 'success' if result['failed'] == 0 else 'partial',
                'records_fetched': result['fetched'],
                'records_processed': result['processed'],
                'records_failed': result['failed'],
                'end_date': fields.Datetime.now()
            })

            self.write({
                'last_sync_date': fields.Datetime.now(),
                'state': 'active'
            })

            _logger.info(f"Sync completed for {self.name}: {result}")
            return result

        except Exception as e:
            _logger.error(f"Sync error for {self.name}: {e}")
            sync_log.write({
                'state': 'error',
                'error_message': str(e),
                'end_date': fields.Datetime.now()
            })
            self.write({'state': 'error'})
            raise