from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime, timedelta, time
import pytz
import logging

_logger = logging.getLogger(__name__)


class AttendanceShift(models.Model):
    _name = 'attendance.shift'
    _description = 'Work Shift Configuration'
    _order = 'sequence, name'

    name = fields.Char(string='Shift Name', required=True)
    code = fields.Char(string='Code', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    is_default = fields.Boolean(string='Default Shift', default=False)

    # Work Hours
    work_hour_from = fields.Float(string='Shift Start', default=9.0, required=True)
    work_hour_to = fields.Float(string='Shift End', default=17.0, required=True)
    expected_hours = fields.Float(string='Expected Hours', compute='_compute_hours', store=True)
    is_night_shift = fields.Boolean(string='Night Shift', compute='_compute_hours', store=True)

    # Late / Early Rules
    late_after_minutes = fields.Integer(string='Late After (minutes)', default=15)
    early_leave_before_minutes = fields.Integer(string='Early Leave Before (minutes)', default=15)
    half_day_hours = fields.Float(string='Half Day If Less Than (hours)', default=4.0)

    # Overtime
    overtime_after_hours = fields.Float(string='Overtime After (hours)', default=8.0)

    # Punch Processing
    min_punch_gap_minutes = fields.Float(string='Min Gap Between Punches (min)', default=1.0)
    auto_checkout_after_hours = fields.Float(string='Auto Checkout After (hours)', default=16.0)

    # Slot System
    use_punch_slots = fields.Boolean(string='Use Time-Based Punch Slots', default=False)
    punch_slot_ids = fields.One2many('attendance.punch.slot', 'shift_id', string='Punch Slots')

    # Display
    work_time_display = fields.Char(string='Work Hours', compute='_compute_display')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    _sql_constraints = [
        ('code_company_unique', 'UNIQUE(code, company_id)', 'Shift code must be unique per company!'),
    ]

    @api.depends('work_hour_from', 'work_hour_to')
    def _compute_hours(self):
        for record in self:
            if record.work_hour_to < record.work_hour_from:
                record.is_night_shift = True
                record.expected_hours = (24 - record.work_hour_from) + record.work_hour_to
            else: 
                record.is_night_shift = False
                record.expected_hours = record.work_hour_to - record.work_hour_from

    @api.depends('work_hour_from', 'work_hour_to')
    def _compute_display(self):
        for record in self: 
            def fmt(f):
                h, m = int(f), int((f - int(f)) * 60)
                return f"{h:02d}:{m:02d}"
            record.work_time_display = f"{fmt(record.work_hour_from)} - {fmt(record.work_hour_to)}"

    @api.constrains('is_default')
    def _check_single_default(self):
        for record in self:
            if record.is_default: 
                others = self.search([
                    ('is_default', '=', True),
                    ('company_id', '=', record.company_id.id),
                    ('id', '!=', record.id)
                ])
                if others:
                    raise ValidationError(_('Only one default shift allowed per company!'))

    def get_shift_boundaries(self, check_date, timezone='UTC'):
        """Get shift start/end times for a specific date"""
        self.ensure_one()

        if isinstance(check_date, datetime):
            check_date = check_date.date()

        try:
            tz = pytz.timezone(timezone)
        except Exception:
            tz = pytz.UTC

        # Shift start
        start_hour = int(self.work_hour_from)
        start_min = int((self.work_hour_from - start_hour) * 60)
        shift_start = tz.localize(datetime.combine(check_date, time(start_hour, start_min)))

        # Shift end
        end_hour = int(self.work_hour_to)
        end_min = int((self.work_hour_to - end_hour) * 60)
        if self.is_night_shift:
            shift_end = tz.localize(datetime.combine(check_date + timedelta(days=1), time(end_hour, end_min)))
        else:
            shift_end = tz.localize(datetime.combine(check_date, time(end_hour, end_min)))

        return {
            'shift_start': shift_start.astimezone(pytz.UTC).replace(tzinfo=None),
            'shift_end': shift_end.astimezone(pytz.UTC).replace(tzinfo=None),
            'late_threshold': (shift_start + timedelta(minutes=self.late_after_minutes)).astimezone(pytz.UTC).replace(tzinfo=None),
            'early_leave_threshold': (shift_end - timedelta(minutes=self.early_leave_before_minutes)).astimezone(pytz.UTC).replace(tzinfo=None),
        }

    @api.model
    def get_employee_shift(self, employee):
        """Get shift for employee (or default)"""
        if hasattr(employee, 'shift_id') and employee.shift_id:
            return employee.shift_id

        company_id = employee.company_id.id if employee.company_id else None
        return self.search([
            ('is_default', '=', True),
            ('company_id', 'in', [company_id, False])
        ], limit=1)

    def create_default_slots(self):
        """Create default punch slots"""
        self.ensure_one()
        self.punch_slot_ids.unlink()

        slots = [
            ('Check In', '0', max(0, self.work_hour_from - 2), self.work_hour_from + 2, True),
            ('Break Out', '2', 12.0, 14.0, False),
            ('Break In', '3', 12.5, 15.0, False),
            ('Check Out', '1', self.work_hour_to - 1, min(23.99, self.work_hour_to + 4), True),
            ('Overtime Start', '4', self.work_hour_to, 23.0, False),
            ('Overtime End', '5', self.work_hour_to + 1, 23.99, False),
        ]

        for seq, (name, ptype, tfrom, tto, req) in enumerate(slots, 1):
            self.env['attendance.punch.slot'].create({
                'shift_id': self.id,
                'name': name,
                'punch_type': ptype,
                'time_from': tfrom,
                'time_to': tto,
                'is_required': req,
                'sequence': seq * 10,
            })

        self.use_punch_slots = True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Default slots created'),
                'type': 'success',
            }
        }