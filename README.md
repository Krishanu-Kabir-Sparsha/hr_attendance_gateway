# Universal Attendance Gateway for Odoo CE v18

Connect any attendance device with Odoo's HR Attendance module.

## Features

- **Multi-Device Support**: ZKTeco, Hikvision, Suprema, Generic APIs, Webhooks
- **Real-time Sync**: Automatic and manual synchronization
- **Smart Processing**: Duplicate detection, auto check-in/out pairing
- **Employee Mapping**: Flexible device user to employee mapping
- **Comprehensive Logging**: Track all sync operations and errors
- **Timezone Support**: Automatic timezone conversion
- **Multi-company**: Full multi-company support

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Install the module in Odoo

3. Go to Attendance Gateway > Devices

4. Create and configure your device

## Supported Devices

### ZKTeco Devices
- All ZK series biometric devices
- F18, K40, iClock360, etc.
- Connection via TCP/IP

### Generic REST API
- Any device with REST API
- Configurable endpoints

### Webhook Push
- Devices that push data to your server
- Secure token-based authentication

## Configuration

1. Create Device: Attendance Gateway > Devices > Create
2. Configure connection details
3. Test connection
4. Fetch users from device
5. Map device users to Odoo employees
6. Activate device
7. Sync attendance

## Usage

### Manual Sync
1. Open device
2. Click "Sync Now"
3. View logs in "Raw Logs" menu

### Automatic Sync
- Configured devices sync automatically every 15 minutes
- Adjust interval in Settings

### Webhook Setup
1. Create webhook device
2. Copy webhook URL
3. Configure in your device
4. Device will push attendance data automatically

## Support

For issues and questions, contact your Odoo partner.

## License

LGPL-3