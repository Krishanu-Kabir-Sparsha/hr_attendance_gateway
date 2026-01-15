from odoo import models, fields, api
import pytz


class AttendancePunchSlot(models.Model):
    _name = 'attendance.punch.slot'
    _description = 'Punch Time Slot'
    _order = 'shift_id, sequence'

    shift_id = fields.Many2one('attendance.shift', string='Shift', required=True, ondelete='cascade')
    name = fields.Char(string='Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)

    punch_type = fields.Selection([
        ('0', 'Check In'),
        ('1', 'Check Out'),
        ('2', 'Break Out'),
        ('3', 'Break In'),
        ('4', 'Overtime Start'),
        ('5', 'Overtime End'),
    ], string='Punch Type', required=True)

    time_from = fields.Float(string='From Time', required=True)
    time_to = fields.Float(string='To Time', required=True)
    is_required = fields.Boolean(string='Required', default=False)
    active = fields.Boolean(string='Active', default=True)

    time_display = fields.Char(string='Time Window', compute='_compute_time_display')

    @api.depends('time_from', 'time_to')
    def _compute_time_display(self):
        for record in self: 
            def fmt(f):
                h, m = int(f), int((f - int(f)) * 60)
                return f"{h:02d}:{m:02d}"
            record.time_display = f"{fmt(record.time_from)} - {fmt(record.time_to)}"

    def is_time_in_window(self, check_time, timezone='UTC'):
        """Check if given time falls within this slot's window"""
        self.ensure_one()

        try:
            tz = pytz.timezone(timezone)
        except Exception:
            tz = pytz.UTC

        if check_time.tzinfo is None:
            check_time = pytz.UTC.localize(check_time)

        local_time = check_time.astimezone(tz)
        current_hour = local_time.hour + local_time.minute / 60.0

        # Handle cross-midnight slots
        if self.time_to < self.time_from:
            return current_hour >= self.time_from or current_hour <= self.time_to
        else:
            return self.time_from <= current_hour <= self.time_to