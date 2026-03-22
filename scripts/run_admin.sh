#!/bin/bash
# Launch the admin dashboard locally

# Check for .env file and load it
if [ -f .env ]; then
    echo "Loading environment variables from .env..."
    export $(grep -v '^#' .env | xargs)
fi

# Set default admin password if not set
if [ -z "$ADMIN_PASSWORD" ]; then
    echo "ADMIN_PASSWORD not set - using default password 'admin'"
    export ADMIN_PASSWORD=admin
else
    echo "Using ADMIN_PASSWORD from environment"
fi

echo ""
echo "======================================"
echo "Admin Dashboard"
echo "======================================"
echo ""
echo "Starting admin dashboard on http://localhost:7861"
echo "Username: admin"
echo "Password: $ADMIN_PASSWORD"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Run the admin dashboard
uv run src/admin_dashboard.py
