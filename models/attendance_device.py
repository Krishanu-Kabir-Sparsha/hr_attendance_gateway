from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
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
    webhook_url = fields.Char(string='Webhook URL', compute='_compute_webhook_url', store=False)

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

    is_online = fields.Boolean(string='Online', compute='_compute_is_online', store=False)

    # Timezone
    timezone = fields.Selection(
        selection='_get_timezones',
        string='Device Timezone',
        default='UTC',
        required=True
    )

    # Statistics
    total_users = fields.Integer(string='Total Users', compute='_compute_statistics', store=False)
    total_logs = fields.Integer(string='Total Logs', compute='_compute_statistics', store=False)
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
            try:
                record.is_online = record._check_device_online()
            except: 
                record.is_online = False

    def _compute_statistics(self):
        for record in self:
            record.total_users = len(record.device_user_ids)
            record.total_logs = len(record.raw_log_ids)

    def _check_device_online(self):
        """Check if device is online"""
        self.ensure_one()
        if self.state != 'active':
            return False
        try:
            adapter = self._get_adapter()
            return adapter.test_connection()
        except Exception as e:
            _logger.warning(f"Device {self.name} connection check failed: {str(e)}")
            return False

    def _get_adapter(self):
        """Factory method to get appropriate adapter"""
        adapter_map = {
            'zkteco': 'ZKTecoAdapter',
            'hikvision': 'HikvisionAdapter',
            'suprema': 'SupremaAdapter',
            'api_rest': 'RestAPIAdapter',
            'api_soap': 'SoapAdapter',
            'webhook': 'WebhookAdapter',
        }

        adapter_class_name = adapter_map.get(self.device_type)
        if not adapter_class_name:
            raise UserError(_("Unsupported device type: %s") % self.device_type)

        # Import from adapters module
        from odoo.addons.hr_attendance_gateway import adapters
        adapter_class = getattr(adapters, adapter_class_name, None)

        if not adapter_class: 
            raise UserError(_("Adapter class %s not found") % adapter_class_name)

        return adapter_class(self)

    def _find_employee_by_badge(self, badge_id):
        """
        Find employee by badge ID using multiple strategies.
        Safely checks if fields exist before searching.
        
        : param badge_id: The badge/ID to search for
        :return: tuple (employee record or None, match_method string or None)
        """
        Employee = self.env['hr.employee']
        employee_fields = Employee._fields
        
        # Strategy 1: identification_id (Standard Odoo field - Employee ID/Badge)
        if 'identification_id' in employee_fields:
            employee = Employee.search([
                ('identification_id', '=', badge_id),
                ('company_id', 'in', [self.company_id.id, False])
            ], limit=1)
            if employee: 
                return employee, 'identification_id'
        
        # Strategy 2: barcode (May exist if hr_attendance is installed)
        if 'barcode' in employee_fields:
            employee = Employee.search([
                ('barcode', '=', badge_id),
                ('company_id', 'in', [self.company_id.id, False])
            ], limit=1)
            if employee:
                return employee, 'barcode'
        
        # Strategy 3: pin (Attendance PIN - if exists)
        if 'pin' in employee_fields:
            employee = Employee.search([
                ('pin', '=', badge_id),
                ('company_id', 'in', [self.company_id.id, False])
            ], limit=1)
            if employee:
                return employee, 'pin'
        
        return None, None

    def action_test_connection(self):
        """Test device connection"""
        self.ensure_one()
        try:
            adapter = self._get_adapter()
            result = adapter.test_connection()
            if result:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Device connection successful!'),
                        'type': 'success',
                        'sticky': False,
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
        """Fetch users from device with smart Badge ID extraction"""
        self.ensure_one()
        try:
            adapter = self._get_adapter()
            users = adapter.get_users()

            created_count = 0
            updated_count = 0
            auto_mapped_count = 0

            for user_data in users:
                device_user_id = str(user_data['device_user_id'])
                device_user_name = user_data.get('name', '')

                # Try to extract badge ID from user name (e.g., "NN-60910013")
                extracted_badge = None
                if device_user_name:
                    numbers = re.findall(r'\d{6,}', device_user_name)  # Find numbers with 6+ digits
                    if numbers:
                        extracted_badge = numbers[0]

                # Use extracted badge as device_user_id if found
                final_device_user_id = extracted_badge or device_user_id

                # Check if mapping already exists
                existing = self.env['attendance.device.user'].search([
                    ('device_id', '=', self.id),
                    ('device_user_id', '=', final_device_user_id)
                ], limit=1)

                # Try to auto-match employee using safe method
                employee, match_method = self._find_employee_by_badge(final_device_user_id)

                vals = {
                    'device_id': self.id,
                    'device_user_id': final_device_user_id,
                    'device_user_name': device_user_name,
                    'card_number': user_data.get('card_number') or '',
                }

                if employee:
                    vals.update({
                        'employee_id': employee.id,
                        'mapping_confidence': 'high',
                        'mapping_method': f'Auto-matched during fetch ({match_method})'
                    })
                    auto_mapped_count += 1

                if not existing:
                    self.env['attendance.device.user'].create(vals)
                    created_count += 1
                else:
                    # Update if employee found but not mapped
                    if employee and not existing.employee_id:
                        existing.write({
                            'employee_id': employee.id,
                            'mapping_confidence': 'high',
                            'mapping_method': f'Auto-matched during fetch ({match_method})'
                        })
                        updated_count += 1

            message = _('Fetched %d users from device\n') % len(users)
            message += _('- Created: %d new mappings\n') % created_count
            if updated_count > 0:
                message += _('- Updated: %d mappings\n') % updated_count
            if auto_mapped_count > 0:
                message += _('- Auto-mapped: %d employees') % auto_mapped_count

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': message,
                    'type': 'success',
                    'sticky': True,
                }
            }
        except Exception as e:
            _logger.error(f"Failed to fetch users from device {self.name}: {str(e)}", exc_info=True)
            raise UserError(_("Failed to fetch users: %s") % str(e))

    def action_activate(self):
        """Activate device"""
        for record in self:
            record.write({'state': 'active'})

    def action_deactivate(self):
        """Deactivate device"""
        for record in self:
            record.write({'state': 'inactive'})

    def action_view_logs(self):
        """View raw logs"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Raw Logs'),
            'res_model': 'attendance.raw.log',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id}
        }

    def action_view_sync_logs(self):
        """View sync logs"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync Logs'),
            'res_model': 'attendance.sync.log',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id}
        }

    @api.model
    def cron_sync_attendance(self):
        """Called by cron job to sync all active devices"""
        devices = self.search([
            ('state', '=', 'active'),
            ('auto_sync', '=', True),
            ('sync_mode', 'in', ['pull', 'both'])
        ])

        for device in devices:
            try:
                device._sync_attendance_logs()
            except Exception as e:
                _logger.error(f"Sync failed for device {device.name}: {str(e)}")
                device.message_post(
                    body=_("Automatic sync failed: %s") % str(e),
                    subject=_("Attendance Sync Error")
                )

    def _sync_attendance_logs(self):
        """Core sync logic"""
        self.ensure_one()

        # Create sync log
        sync_log = self.env['attendance.sync.log'].create({
            'device_id': self.id,
            'sync_date': fields.Datetime.now(),
            'state': 'in_progress'
        })

        try:
            adapter = self._get_adapter()

            # Get logs from device
            from_date = self.last_sync_date or (fields.Datetime.now() - timedelta(days=7))
            raw_logs = adapter.get_attendance_logs(from_date=from_date)

            # Process logs
            processor = self.env['attendance.processor']
            result = processor.process_raw_logs(self, raw_logs)

            # Update sync log
            sync_log.write({
                'state': 'success',
                'records_fetched': result['fetched'],
                'records_processed': result['processed'],
                'records_failed': result['failed'],
                'end_date': fields.Datetime.now()
            })

            # Update device
            self.write({
                'last_sync_date': fields.Datetime.now(),
                'state': 'active'
            })

            _logger.info(f"Sync successful for device {self.name}: {result}")

        except Exception as e: 
            _logger.error(f"Sync error for device {self.name}: {str(e)}")
            sync_log.write({
                'state': 'error',
                'error_message': str(e),
                'end_date': fields.Datetime.now()
            })
            self.write({'state': 'error'})
            raise