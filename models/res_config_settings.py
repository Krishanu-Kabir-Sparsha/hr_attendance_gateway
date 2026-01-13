# ===================================================================
# FILE: models/res_config_settings.py
# ===================================================================

from odoo import models, fields, api, _
from datetime import timedelta

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Global Attendance Gateway Settings
    attendance_gateway_auto_sync = fields.Boolean(
        string='Enable Auto Sync',
        config_parameter='attendance_gateway.auto_sync',
        default=True,
        help='Enable automatic synchronization for all active devices'
    )
    
    attendance_gateway_sync_interval = fields.Integer(
        string='Default Sync Interval (minutes)',
        config_parameter='attendance_gateway.sync_interval',
        default=15,
        help='Default synchronization interval for new devices'
    )
    
    attendance_gateway_keep_raw_logs = fields.Integer(
        string='Keep Raw Logs (days)',
        config_parameter='attendance_gateway.keep_raw_logs',
        default=90,
        help='Number of days to keep raw attendance logs (0 = keep forever)'
    )
    
    attendance_gateway_keep_sync_logs = fields.Integer(
        string='Keep Sync Logs (days)',
        config_parameter='attendance_gateway.keep_sync_logs',
        default=30,
        help='Number of days to keep sync history logs (0 = keep forever)'
    )
    
    attendance_gateway_auto_create_employee = fields.Boolean(
        string='Auto Create Employees',
        config_parameter='attendance_gateway.auto_create_employee',
        default=False,
        help='Automatically create employee records for unmapped device users'
    )
    
    attendance_gateway_duplicate_threshold = fields.Integer(
        string='Duplicate Detection Threshold (seconds)',
        config_parameter='attendance_gateway.duplicate_threshold',
        default=60,
        help='Time window to detect duplicate attendance records (in seconds)'
    )

    
    
    attendance_gateway_notification_email = fields.Char(
        string='Error Notification Email',
        config_parameter='attendance_gateway.notification_email',
        help='Email address to receive sync error notifications'
    )
    
    attendance_gateway_enable_webhook = fields.Boolean(
        string='Enable Webhook Endpoints',
        config_parameter='attendance_gateway.enable_webhook',
        default=True,
        help='Enable webhook endpoints for receiving attendance data'
    )
    
    # Statistics (readonly)
    attendance_gateway_device_count = fields.Integer(
        string='Total Devices',
        compute='_compute_gateway_statistics',
        readonly=True
    )
    
    attendance_gateway_active_device_count = fields.Integer(
        string='Active Devices',
        compute='_compute_gateway_statistics',
        readonly=True
    )
    
    attendance_gateway_pending_logs = fields.Integer(
        string='Pending Logs',
        compute='_compute_gateway_statistics',
        readonly=True
    )
    
    attendance_gateway_error_logs = fields.Integer(
        string='Error Logs',
        compute='_compute_gateway_statistics',
        readonly=True
    )

    # NEW: Punch processing settings (used when no shift is configured)
    attendance_gateway_min_punch_interval = fields.Float(
        string='Minimum Punch Interval (minutes)',
        config_parameter='attendance_gateway.min_punch_interval',
        default=1.0,
        help='Minimum time between punches. Punches closer than this are ignored.'
    )

    # Attendance Validation Settings
    attendance_gateway_min_work_duration = fields.Float(
        string='Minimum Work Duration (hours)',
        config_parameter='attendance_gateway.min_work_duration',
        default=0.1,
        help='Minimum duration for a valid work session (default: 0.1 hours = 6 minutes)'
    )
    
    attendance_gateway_max_work_duration = fields.Float(
        string='Maximum Work Duration (hours)',
        config_parameter='attendance_gateway.max_work_duration',
        default=16.0,
        help='Maximum duration for a single work session (default: 16 hours)'
    )

    attendance_gateway_auto_close_hours = fields.Float(
        string='Auto-close After (hours)',
        config_parameter='attendance_gateway.auto_close_hours',
        default=20.0,
        help='Automatically close open attendances older than this'
    )
    
    attendance_gateway_validate_work_duration = fields.Boolean(
        string='Validate Work Duration',
        config_parameter='attendance_gateway.validate_work_duration',
        default=True,
        help='Enable validation of minimum and maximum work duration'
    )

    @api.depends()
    def _compute_gateway_statistics(self):
        """Compute statistics for display"""
        for record in self:
            record.attendance_gateway_device_count = self.env['attendance.device'].search_count([])
            record.attendance_gateway_active_device_count = self.env['attendance.device'].search_count([
                ('state', '=', 'active')
            ])
            record.attendance_gateway_pending_logs = self.env['attendance.raw.log'].search_count([
                ('state', '=', 'pending')
            ])
            record.attendance_gateway_error_logs = self.env['attendance.raw.log'].search_count([
                ('state', '=', 'error')
            ])
    
    def action_clean_old_logs(self):
        """Clean old logs based on retention settings"""
        self.ensure_one()
        count_raw = 0
        count_sync = 0
        
        # Clean raw logs
        if self.attendance_gateway_keep_raw_logs > 0:
            cutoff_date = fields.Datetime.now() - timedelta(days=self.attendance_gateway_keep_raw_logs)
            old_raw_logs = self.env['attendance.raw.log'].search([
                ('timestamp', '<', cutoff_date),
                ('state', 'in', ['processed', 'ignored', 'duplicate'])
            ])
            count_raw = len(old_raw_logs)
            old_raw_logs.unlink()
        
        # Clean sync logs
        if self.attendance_gateway_keep_sync_logs > 0:
            cutoff_date = fields.Datetime.now() - timedelta(days=self.attendance_gateway_keep_sync_logs)
            old_sync_logs = self.env['attendance.sync.log'].search([
                ('sync_date', '<', cutoff_date),
                ('state', 'in', ['success', 'partial'])
            ])
            count_sync = len(old_sync_logs)
            old_sync_logs.unlink()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Cleanup Complete'),
                'message': _('Deleted %d raw logs and %d sync logs') % (count_raw, count_sync),
                'type': 'success',
                'sticky': False,
            }
        }
    
    def action_reprocess_error_logs(self):
        """Reprocess all error logs"""
        error_logs = self.env['attendance.raw.log'].search([('state', '=', 'error')])
        if error_logs:
            error_logs.action_reprocess()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reprocessing'),
                'message': _('Reprocessing %d error logs') % len(error_logs),
                'type': 'info',
            }
        }

    def action_sync_all_devices(self):
        """Sync all active devices"""
        devices = self.env['attendance.device'].search([
            ('state', '=', 'active'),
            ('sync_mode', 'in', ['pull', 'both'])
        ])
        if devices:
            devices.cron_sync_attendance()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Started'),
                'message': _('Syncing %d devices') % len(devices),
                'type': 'success',
            }
        }