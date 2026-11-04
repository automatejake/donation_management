# Donation Management Module for Odoo 18

A comprehensive donation management system for Odoo v18 Community Edition that enables organizations to accept one-time and recurring donations through their website, with full payment provider integration and donor portal access.

## Features

### 🎯 Core Functionality
- **Online Donation Forms**: Beautiful, responsive donation forms integrated with Odoo Website Builder
- **Recurring Donations**: Support for monthly, quarterly, and yearly recurring donations
- **Payment Integration**: Works with any Odoo payment provider (Stripe, PayPal, Authorize.net, etc.)
- **Payment Tokens**: Secure storage of payment methods for recurring donations
- **Donor Portal**: Self-service portal where donors can view history and manage recurring donations
- **Campaign Management**: Create and track fundraising campaigns with goals and progress
- **Tax Receipts**: Automatic generation of tax receipt numbers for donations
- **Year-End Giving Statements**: One statement per donor listing every confirmed gift in a date range, as a PDF and by email (built on OCA `donation_base`)

### 💳 Payment Features
- Support for all native Odoo payment providers
- Secure payment token storage for recurring donations
- Automatic recurring payment processing via cron job
- Payment failure handling and donor notifications
- One-click payment using saved payment methods

### 👥 Donor Portal
Donors can log in to:
- View complete donation history
- See all recurring donations with next payment dates
- Pause, resume, or cancel recurring donations
- Download tax receipts
- Make new donations

### 📊 Backend Management
- Complete donation tracking and reporting
- Recurring donation rule management
- Campaign creation and monitoring
- Donor relationship management
- Integration with Odoo accounting (optional invoice creation)

## Year-End Giving Statements

Donations > Donations > Year-End Statements.

1. **Create Statements**: pick the date range (defaults to last calendar year). One statement is made per donor from their confirmed donations, using the donor's company if they have one. Donors whose "Tax Receipt Option" is set to "None" are skipped. A gift goes on at most one statement, and running it again for the same period is refused.
2. **Email Statements**: select statements in the list and email them with the PDF attached. Donors with no email address are named so their statements can be printed instead.
3. The note printed at the bottom (by default "No goods or services were provided in exchange for these contributions, other than intangible religious benefits.") can be changed per company under Settings > Companies > Donations.

Statements use OCA `donation_base` (annual tax receipts). OCA's `donation` module can't be installed alongside this one, because both define `donation.donation`.

## Installation

1. **Copy Module to Addons**
   ```bash
   cp -r donation_management /path/to/odoo/addons/
   ```

2. **Update Apps List**
   - Go to Apps menu in Odoo
   - Click "Update Apps List"
   - Search for "Donation Management"

3. **Install Module**
   - Click "Install" on the Donation Management module

4. **Configure Payment Providers**
   - Go to Website > Configuration > Payment Providers
   - Configure your desired payment providers (Stripe, PayPal, etc.)
   - Make sure to enable tokenization for recurring donations

## Configuration

### Initial Setup

1. **Configure Payment Providers**
   - Navigate to Website > Configuration > Payment Providers
   - Set up at least one payment provider
   - Enable "Save Cards" / "Tokenization" for recurring donations

2. **Create Donation Campaigns** (Optional)
   - Go to Donations > Configuration > Campaigns
   - Create campaigns with goals and descriptions
   - Campaigns will appear in the donation form dropdown

3. **Set User Permissions**
   - Assign users to "Donation Manager" or "Donation User" groups
   - Found in Settings > Users & Companies > Groups

### Website Integration

1. **Add Donation Page to Menu**
   - Go to Website > Site > Menu Editor
   - Add a menu item linking to `/donate`

2. **Customize Donation Form** (Optional)
   - Navigate to Website > Site
   - Visit `/donate` page
   - Use Website Builder to customize the page layout

### Recurring Donations

The module includes a scheduled action that runs daily to process recurring donations:
- **Cron Job**: "Process Recurring Donations"
- **Frequency**: Daily
- **Action**: Checks for due recurring donations and processes payments

To modify the frequency:
1. Go to Settings > Technical > Automation > Scheduled Actions
2. Find "Process Recurring Donations"
3. Adjust the interval as needed

## Usage

### Making a Donation (Website)

1. Visitors go to `/donate` on your website
2. Select donation amount (preset or custom)
3. Choose to make it recurring (optional)
4. Select or enter donor information
5. Choose payment method
6. Complete payment through the payment provider

### Managing Donations (Backend)

1. **View All Donations**
   - Donations > Donations > All Donations
   - Filter by status, campaign, donor, date range

2. **Manage Recurring Donations**
   - Donations > Donations > Recurring Donations
   - View upcoming payment dates
   - Pause, resume, or cancel rules

3. **Track Campaigns**
   - Donations > Configuration > Campaigns
   - Monitor progress toward goals
   - View donations per campaign

### Donor Portal Access

Donors can access their donation history at:
- `/my/donations` - View all donations and manage recurring donations

Portal features:
- Complete donation history with amounts and dates
- Active recurring donation management (pause/resume/cancel)
- Download tax receipts
- Make new donations

## Technical Details

### Models

- **donation.donation**: Main donation record
- **donation.recurring.rule**: Recurring donation configuration
- **donation.campaign**: Fundraising campaign
- **res.partner**: Extended with donation statistics

### Controllers

- `/donate` - Main donation form page
- `/donate/submit` - Process donation form submission
- `/donate/confirmation/<id>` - Donation confirmation page
- `/my/donations` - Portal donation list
- `/my/donations/<id>` - Portal donation detail
- `/my/recurring_donations/<id>/[pause|resume|cancel]` - Manage recurring donations

### Security

- **Donation Manager**: Full access to all donations and configuration
- **Donation User**: Read-only access to donations
- **Portal Users**: Access only to their own donations
- **Public**: Can submit donations via website form

### Payment Integration

The module integrates with Odoo's payment system:
1. Uses standard `payment.transaction` for all payments
2. Supports `payment.token` for saved payment methods
3. Handles callbacks from payment providers
4. Processes recurring payments using saved tokens

## Customization

### Custom Fields

Add custom fields to donations by inheriting the model:

```python
from odoo import models, fields

class DonationCustom(models.Model):
    _inherit = 'donation.donation'
    
    custom_field = fields.Char(string='Custom Field')
```

### Custom Email Templates

Modify email templates:
1. Go to Settings > Technical > Email Templates
2. Find "Donation: Confirmation" or "Donation: Recurring Payment Failed"
3. Edit the HTML content as needed

### Custom Donation Form

The donation form template can be customized:
- Edit `donation_website_templates.xml`
- Modify the `donation_form_page` template
- Add/remove fields as needed

## Troubleshooting

### Recurring Donations Not Processing

Check:
1. Cron job is active (Settings > Technical > Scheduled Actions)
2. Payment tokens are valid and not expired
3. Payment provider is properly configured
4. Check donation rule state (should be "Active")

### Payment Failures

Common issues:
1. Payment provider credentials incorrect
2. Test mode enabled when using live transactions
3. Payment token expired or invalid
4. Insufficient funds or card issues

### Portal Access Issues

Ensure:
1. User has portal access enabled
2. User's partner_id matches donation partner_id
3. Donation state is "confirmed"

## Support & Contribution

For bugs, questions, or feature requests, please contact your Odoo administrator or the module developer.

## License

LGPL-3

## Credits

Developed for Odoo 18 Community Edition
Compatible with all standard Odoo payment providers