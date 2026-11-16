# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Modelryx, please report it responsibly using [GitHub private vulnerability reporting](https://github.com/Modelryx/Modelryx/security/advisories/new). Do not include vulnerability details in public issues.

**Please do not publicly disclose the vulnerability until it has been addressed.**

When reporting a security issue, please include:

- Description of the vulnerability
- Steps to reproduce (if possible)
- Potential impact
- Suggested fix (if you have one)

We will:

1. Acknowledge receipt of your report within 24 hours
2. Assess the severity and impact
3. Work on a fix and release a patched version as soon as possible
4. Credit you for the discovery (unless you prefer anonymity)

## Supported Versions

Security updates are provided for:

- The latest stable release (minor and patch updates)
- The current development version

Older versions may receive critical security fixes at the maintainers' discretion.

## Security Best Practices

### For Users

1. **Keep dependencies updated** - Regularly update Modelryx and its dependencies
2. **Use strong credentials** - Set secure passwords and API keys in your .env
3. **Secure your deployment** - Run Modelryx behind a reverse proxy with TLS
4. **Restrict access** - Limit network access to Modelryx based on your needs
5. **Monitor logs** - Regularly review application and system logs for suspicious activity

### For Developers

1. **Input validation** - Always validate and sanitize user input
2. **Secure secrets** - Never commit secrets to version control; use environment variables
3. **Dependencies** - Keep dependencies up-to-date and review security advisories
4. **Code review** - All changes should be reviewed before merging
5. **Testing** - Write tests for security-critical functionality

## Known Security Considerations

### Worker Communication

- Worker authentication depends on the worker contract; the STEP worker uses a shared key header
- Worker secrets should be unique per deployment
- Use TLS for worker communication over untrusted networks

### Database Access

- PostgreSQL connection uses authentication
- Use connection pooling with appropriate timeouts
- Regular backups are essential for disaster recovery

### API Security

- Endpoints should be protected with authentication
- Rate limiting should be configured in production
- CORS should be appropriately restricted

## Reporting Non-Security Issues

For bug reports and feature requests, use the public [GitHub Issues](https://github.com/modelryx/modelryx/issues) tracker.

## Additional Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Python Security Best Practices](https://python.readthedocs.io/en/latest/library/security_warnings.html)
- [Node.js Security Best Practices](https://nodejs.org/en/docs/guides/security/)
- [docs/security/worker-security.md](./docs/security/worker-security.md) - Detailed worker security model

## Security Contacts

Use [Report a vulnerability](https://github.com/Modelryx/Modelryx/security/advisories/new) on GitHub. Maintainers must enable private vulnerability reporting in the repository security settings before publication. If the form is unavailable, open an issue requesting that private reporting be enabled, without including vulnerability details.
