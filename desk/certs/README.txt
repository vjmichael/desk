cacert.pem is the Mozilla root certificate bundle, copied unmodified from the
Python "certifi" package (licence: MPL-2.0). Desk uses it ONLY as a fallback when
your computer's own certificate store cannot verify a site. Verification is never
turned off. Refresh it by copying a newer certifi bundle over this file.
