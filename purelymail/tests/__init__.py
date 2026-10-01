"""Test package. Importing admin puts ../vendor (bundled dependencies) on sys.path."""

import admin

VENDOR_DIR = admin._VENDOR
