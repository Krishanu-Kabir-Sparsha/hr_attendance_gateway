{
    'name': 'Universal Attendance Gateway',
    'version': '18.0.1.0.0',
    'category': 'Human Resources/Attendances',
    'summary': 'Connect any attendance device with Odoo Attendance module',
    'description': """
        Universal Attendance Gateway
        ============================
        Connect multiple types of attendance devices with Odoo:
        * ZKTeco biometric devices
        * Hikvision access control
        * Suprema BioStar
        * Generic REST/SOAP APIs
        * Webhook-based devices
        
        Features:
        * Multi-device support
        * Real-time and scheduled sync
        * Automatic duplicate detection
        * Employee-device user mapping
        * Comprehensive error logging
        * Timezone support
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'license': 'LGPL-3',
    'depends': ['hr_attendance', 'mail'],
    'external_dependencies': {
        'python': ['pyzk', 'requests', 'pytz'],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        
        # WIZARDS FIRST (before views that reference them)
        'wizards/sync_attendance_wizard_views.xml',
        'wizards/device_test_wizard_views.xml',
        'wizards/user_mapping_wizard_views.xml',
        'wizards/manual_attendance_wizard_views.xml',  # MUST BE BEFORE attendance_raw_log_views.xml
        
        # THEN VIEWS
        'views/attendance_shift_views.xml',
        # 'views/attendance_daily_punch_views.xml',
        'views/attendance_device_views.xml',
        'views/attendance_device_user_views.xml',
        'views/attendance_raw_log_views.xml',  # This references the wizard action
        'views/attendance_sync_log_views.xml',
        'views/hr_attendance_views.xml',
        'views/hr_employee_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
}
