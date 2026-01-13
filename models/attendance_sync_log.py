from odoo import models, fields, api

class AttendanceSyncLog(models.Model):
    _name = 'attendance.sync.log'
    _description = 'Sync History Log'
    _order = 'sync_date desc'
    
    device_id = fields.Many2one('attendance.device', string='Device', required=True, ondelete='cascade')
    sync_date = fields.Datetime(string='Start Time', required=True, default=fields.Datetime.now)
    end_date = fields.Datetime(string='End Time')
    
    state = fields.Selection([
        ('in_progress', 'In Progress'),
        ('success', 'Success'),
        ('partial', 'Partial Success'),
        ('error', 'Error'),
    ], string='Status', default='in_progress')
    
    records_fetched = fields.Integer(string='Records Fetched', default=0)
    records_processed = fields.Integer(string='Records Processed', default=0)
    records_failed = fields.Integer(string='Records Failed', default=0)
    
    error_message = fields.Text(string='Error Message')
    duration = fields.Float(string='Duration (seconds)', compute='_compute_duration', store=True)
    
    company_id = fields.Many2one('res.company', string='Company', related='device_id.company_id', store=True)
    
    @api.depends('sync_date', 'end_date')
    def _compute_duration(self):
        for record in self:
            if record.sync_date and record.end_date:
                delta = record.end_date - record.sync_date
                record.duration = delta.total_seconds()
            else:
                record.duration = 0.0
    
    def name_get(self):
        result = []
        for record in self:
            name = f"{record.device_id.name} - {record.sync_date.strftime('%Y-%m-%d %H:%M')}"
            result.append((record.id, name))
        return result