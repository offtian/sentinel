---
name: auth-error-response
description: Response template for authentication and permission-denied support tickets
version: 1.0.0
applies_to: ["auth_*", "permission_denied"]
---

# Auth Error Response Template

## 1. Classify the Auth Error

Determine the error category from the ticket details:

| Error Pattern | Category | Typical Cause |
|--------------|----------|---------------|
| `401 Unauthorized` | Token/API key invalid or expired | Key rotation, token expiry |
| `403 Forbidden` | Insufficient permissions | Missing role assignment, segregation of duties |
| `MFA required` | MFA challenge not completed | New device, policy enforcement |
| `Account locked` | Too many failed attempts | Brute force protection triggered |
| `Certificate expired` | mTLS cert expiry | Service certificate rotation missed |

## 2. API Key Issues

### Expired or Rotated Keys

Draft response:
> Your API key may have been rotated as part of our scheduled key rotation cycle. API keys for trading platform access are rotated every 90 days per our security policy.
>
> To resolve this:
> 1. Log in to the API Management Portal at `https://portal.internal/api-keys`.
> 2. Generate a new API key for your application.
> 3. Update your application configuration with the new key.
> 4. Verify connectivity by calling the `/health` endpoint.
>
> If you need an extended key lifetime for a production integration, submit a security exception request through ServiceNow referencing policy SEC-API-001.

### Key Rotation Schedule

| Environment | Rotation Period | Next Rotation |
|------------|----------------|---------------|
| Production trading | 90 days | Check Vault metadata |
| UAT/staging | 180 days | Check Vault metadata |
| Service accounts | 365 days (with approval) | Check Vault metadata |

Verify the key status in Vault:
```bash
vault read secret/data/api-keys/<service-name> -format=json | jq '.data.data.expiry'
```

## 3. OAuth Token Expiry

Draft response:
> OAuth access tokens for the trading platform expire after 1 hour. Refresh tokens are valid for 24 hours. If both have expired, you will need to re-authenticate.
>
> Common causes of token issues:
> - Your application is not using the refresh token flow. Implement the `grant_type=refresh_token` exchange before the access token expires.
> - The authorization server underwent maintenance, invalidating active sessions.
> - Your client secret was rotated. Check with your team lead if a rotation was performed.
>
> To re-authenticate, use the standard OAuth2 flow at `https://auth.internal/oauth2/authorize`.

Check the token status in the auth service logs:
```
auth.token.validation{client_id:<client-id>,status:rejected} by {reason}
```

## 4. MFA Enforcement

Draft response:
> Multi-factor authentication is mandatory for all trading platform access per regulatory compliance requirements (SEC Rule 15c3-5, MiFID II).
>
> If you are seeing MFA prompts unexpectedly:
> - You may be logging in from a new device or IP range not in the trusted network list.
> - Your MFA token may have drifted. Re-sync your authenticator app using the QR code in your profile settings.
> - Hardware token battery may be depleted. Contact the security team for a replacement.
>
> MFA cannot be bypassed or temporarily disabled for any user.

## 5. Service Account Permission Requests

When a user requests elevated permissions for a service account:

1. Verify the requestor is authorized to manage the service account (check ownership in CMDB).
2. Confirm the requested permissions align with the principle of least privilege.
3. Check for segregation of duties violations:
   - A service that **submits orders** must not also have permissions to **approve orders**.
   - A service that **reads position data** must not also have **write access to trade execution**.
   - **Compliance reporting** accounts must not have **trading permissions**.

Draft response for permission escalation:
> Service account permission changes require approval from both the application owner and the security team. Please submit a request through the Access Management Portal with:
> 1. The service account name and current role.
> 2. The specific permissions being requested and business justification.
> 3. Confirmation from your team lead that the request does not violate segregation of duties.
>
> Requests are typically processed within 4 business hours. Emergency access during market hours can be expedited -- contact the security on-call via PagerDuty.

## 6. Response Checklist

Before sending any auth-related response, verify:

- [ ] The response does not reveal whether a specific account exists
- [ ] No internal system names, IPs, or architecture details are exposed
- [ ] The user is directed to the correct self-service portal
- [ ] Escalation path is included if self-service cannot resolve the issue
- [ ] Compliance implications are noted where applicable

## Compliance Note

All authentication failures on trading systems must be retained in audit logs for 7 years per SEC Rule 17a-4. Do not advise users to clear logs or retry repeatedly, as this generates noise in compliance audit trails. If a user reports persistent auth failures, escalate to the security team to rule out unauthorized access attempts.
