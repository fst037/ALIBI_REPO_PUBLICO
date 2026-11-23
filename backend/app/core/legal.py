"""The current version of the Terms and Conditions and the Privacy Policy.

The documents themselves live in the frontend (`frontend/src/features/legal/`,
shown as a modal over any screen), which
carry the same version string (`frontend/src/lib/legal.ts`). Signing up sends
the version the user was shown and accepted; the account records it with the
time (`users.terms_version`, `users.terms_accepted_at`) — the proof of consent
Ley 25.326 asks for, including the express consent to the international
transfer of data.

Bump it together with the frontend constant whenever either text changes in
substance. A sign-up that sends an older version is refused, so nobody accepts
a text other than the one in force.
"""

TERMS_VERSION = "2026-10-11"
