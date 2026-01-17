from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import logging

_logger = logging.getLogger(__name__)


class AttendanceRawLog(models.Model):
    _name = 'attendance.raw.log'
    _description = 'Device Punch Log'
    _order = 'timestamp desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=False)

    device_id = fields.Many2one(
        'attendance.device',
        string='Device',
        required=True,
        ondelete='cascade',
        index=True
    )
    device_user_id = fields.Char(
        string='Device User ID',
        required=True,
        index=True
    )
    timestamp = fields.Datetime(
        string='Punch Time',
        required=True,
        index=True
    )

    # Punch type determined by our system
    punch_type = fields.Selection([
        ('0', 'Check In'),
        ('1', 'Check Out'),
        ('2', 'Break Out'),
        ('3', 'Break In'),
        ('4', 'Overtime Start'),
        ('5', 'Overtime End'),
    ], string='Punch Type', default='0', help='Punch type determined by system')

    state = fields.Selection([
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('ignored', 'Ignored'),
        ('error', 'Error'),
    ], string='Status', default='pending', index=True)

    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
        readonly=True,
        index=True
    )
    attendance_id = fields.Many2one(
        'hr.attendance',
        string='Attendance',
        readonly=True
    )
    message = fields.Char(string='Message')
    raw_data = fields.Text(string='Raw Data')

    company_id = fields.Many2one(
        'res.company',
        related='device_id.company_id',
        store=True
    )

    _sql_constraints = [
        ('unique_log', 'UNIQUE(device_id, device_user_id, timestamp)',
         'Duplicate punch detected!'),
    ]

    @api.depends('employee_id', 'punch_type', 'timestamp')
    def _compute_display_name(self):
        punch_labels = dict(self._fields['punch_type'].selection)
        for record in self:
            emp_name = record.employee_id.name if record.employee_id else record.device_user_id
            punch_label = punch_labels.get(record.punch_type, 'Unknown')
            time_str = record.timestamp.strftime('%Y-%m-%d %H:%M') if record.timestamp else ''
            record.display_name = f"{emp_name} - {punch_label} @ {time_str}"

    def action_reprocess(self):
        """Reprocess selected logs"""
        processor = self.env['attendance.processor']
        success = 0
        failed = 0

        for log in self.filtered(lambda l: l.state in ['pending', 'error', 'ignored']):
            try:
                # Reset state
                log.write({
                    'state': 'pending',
                    'message': False,
                    'attendance_id': False
                })
                
                # Reprocess
                result = processor.process_single_log(log)
                
                if result.get('success'):
                    success += 1
                elif result.get('ignored'):
                    success += 1  # Ignored is still "processed"
                else:
                    failed += 1
                    
            except Exception as e:
                failed += 1
                log.write({'state': 'error', 'message': str(e)[:200]})
                _logger.error(f"Reprocess failed for log {log.id}: {e}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reprocess Complete'),
                'message': _('Success: %d, Failed: %d') % (success, failed),
                'type': 'success' if failed == 0 else 'warning',
            }
        }

    def action_force_checkin(self):
        """Force this punch to be treated as a check-in by closing any open attendance first"""
        self.ensure_one()
        
        if self.state == 'processed': 
            raise UserError(_("Cannot modify an already processed log. Please delete the related attendance first."))

        if not self.employee_id:
            # Try to find employee
            processor = self.env['attendance.processor']
            employee = processor._find_employee_by_badge(self.device_id, self.device_user_id)
            if not employee:
                raise UserError(_("Cannot find employee for this device user. Please map the employee first."))
            self.employee_id = employee.id

        # Close any open attendance
        open_att = self.env['hr.attendance'].search([
            ('employee_id', '=', self.employee_id.id),
            ('check_out', '=', False)
        ])
        
        for att in open_att:
            att.write({
                'check_out': att.check_in + timedelta(minutes=1),
                'note': f"{att.note or ''}\n⚠️ Manually closed to allow forced check-in".strip()
            })
            att._compute_status()

        # Now reprocess
        self.write({'state': 'pending', 'message': 'Forcing as check-in'})
        return self.action_reprocess()

    def action_view_attendance(self):
        """Open related attendance record"""
        self.ensure_one()
        if not self.attendance_id:
            raise UserError(_("No attendance record linked to this punch"))
            
        return {
            'type': 'ir.actions.act_window',
            'name': _('Attendance'),
            'res_model': 'hr.attendance',
            'res_id': self.attendance_id.id,
            'view_mode': 'form',
            'target': 'current',
        }