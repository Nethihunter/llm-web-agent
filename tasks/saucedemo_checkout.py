"""
Demo task: Saucedemo checkout flow.

Demonstrates:
- Login form (2 fields)
- Product page navigation
- Cart management
- Multi-step checkout (3 forms)
- Success verification

Site: https://www.saucedemo.com (public testing site by Sauce Labs)
Legal: 100% allowed — this is the official demo site for automation.
"""

TASK = {
    "name": "checkout",
    "url": "https://www.saucedemo.com",
    "goal": (
        "Log in as standard_user, add the 'Sauce Labs Backpack' "
        "to the cart, proceed to checkout, fill shipping info "
        "(First: Test, Last: User, Zip: 75001), click Finish, "
        "and confirm the order is complete."
    ),
    "variables": {
        "username": "standard_user",
        "password": "secret_sauce",
        "first_name": "Test",
        "last_name": "User",
        "postal_code": "75001",
    },
    "success_hint": "Thank you for your order",
    "max_steps": 20,
}
