-- Closes the email leak: get_login_email(username) was callable by anyone
-- (even signed out) and returned the account's email. Sign-in now uses the
-- email directly (see docs/assets/app.js), so the function is not needed.
-- Safe to run more than once.

drop function if exists public.get_login_email(text);
