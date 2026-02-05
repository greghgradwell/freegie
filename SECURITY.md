# Security Policy

## Security Audit Summary

This repository was recently converted from private to public. A comprehensive security audit was performed on February 5, 2026.

### Findings

#### ✅ No Critical Issues Found

1. **No hardcoded secrets or credentials** - Verified no API keys, passwords, tokens, or private keys in the codebase or git history
2. **No exposed sensitive files** - Confirmed `.env` files and credentials are properly excluded via `.gitignore`
3. **Secure server binding** - Web server correctly binds to `127.0.0.1` (localhost only), not exposed to network
4. **No command injection risks** - No use of `subprocess.call()` with `shell=True` or similar dangerous patterns
5. **Input validation** - All user inputs are properly validated with error handling
6. **No XSS vulnerabilities** - Web UI does not use dangerous patterns like `innerHTML` or `eval()`

#### 🔧 Fixed Issues

1. **Dependency Vulnerabilities** - Updated `aiohttp` from `3.9.0` to `3.13.3` to address:
   - **CVE**: HTTP Parser zip bomb vulnerability (all versions ≤ 3.13.2)
   - **CVE**: Denial of Service in malformed POST requests (versions < 3.9.4)
   - **CVE**: Directory traversal vulnerability (versions 1.0.5 to < 3.9.2)

### Security Considerations

#### Network Exposure

The Freegie daemon binds to `127.0.0.1:7380` by default, making it **only accessible from the local machine**. This design decision means:

- ✅ The web UI is not exposed to the network
- ✅ No authentication is required (acceptable for localhost-only services)
- ⚠️ Users who modify the binding to `0.0.0.0` should implement additional security measures

#### Authentication

The web UI and API endpoints do **not** implement authentication. This is acceptable because:

1. The daemon only binds to localhost by default
2. The service manages a local hardware device (Chargie)
3. Adding authentication would add complexity without security benefit for the primary use case

**Warning**: If you modify the code to bind to a network interface, you should add authentication.

## Reporting a Vulnerability

If you discover a security vulnerability in Freegie, please report it by:

1. **Do not** open a public GitHub issue
2. Email the maintainer directly with details
3. Allow reasonable time for a fix before public disclosure

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Security Best Practices

When using Freegie:

1. **Keep dependencies updated** - Regularly update Python packages, especially `aiohttp` and `bleak`
2. **Review configuration** - Ensure the daemon remains bound to `127.0.0.1`
3. **Monitor access** - Check systemd logs for any unusual activity: `journalctl -u freegie`
4. **Protect config files** - User configuration in `~/.config/freegie/` should have appropriate permissions
5. **Use HTTPS for CDN resources** - The web UI loads some resources from CDNs; ensure your connection is secure

## Security Checklist for Contributors

Before submitting PRs:

- [ ] No hardcoded secrets, API keys, or credentials
- [ ] Input validation on all user-controlled data
- [ ] No command injection risks (`shell=True`, `eval()`, etc.)
- [ ] Dependencies checked for known vulnerabilities
- [ ] Server binding remains localhost-only by default
- [ ] No introduction of unnecessary network exposure
- [ ] Error messages don't leak sensitive information
- [ ] File operations use safe path handling

## Security Update History

### 2026-02-05
- **Dependency Update**: Upgraded `aiohttp` from 3.9.0 to 3.13.3
  - Fixed zip bomb vulnerability in HTTP Parser
  - Fixed DoS vulnerability in POST request handling
  - Fixed directory traversal vulnerability
- **Security Audit**: Complete codebase security review performed
  - No hardcoded secrets found
  - Confirmed secure server binding (localhost only)
  - Verified input validation and XSS protections
