function decodeBase64url(value: string): ArrayBuffer {
  const normalized = value
    .replace(/-/g, '+')
    .replace(/_/g, '/')
    .padEnd(Math.ceil(value.length / 4) * 4, '=');
  const bytes = Uint8Array.from(atob(normalized), (character) => character.charCodeAt(0));
  return bytes.buffer;
}

function encodeBase64url(value: ArrayBuffer | null): string | null {
  if (!value) return null;
  const bytes = new Uint8Array(value);
  let text = '';
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function decodeRequestOptions(
  value: PublicKeyCredentialRequestOptionsJSON,
): PublicKeyCredentialRequestOptions {
  return {
    ...value,
    challenge: decodeBase64url(value.challenge),
    allowCredentials: value.allowCredentials?.map((item) => ({
      ...item,
      id: decodeBase64url(item.id),
    })),
  } as PublicKeyCredentialRequestOptions;
}

export function decodeCreationOptions(
  value: PublicKeyCredentialCreationOptionsJSON,
): PublicKeyCredentialCreationOptions {
  return {
    ...value,
    challenge: decodeBase64url(value.challenge),
    user: { ...value.user, id: decodeBase64url(value.user.id) },
    excludeCredentials: value.excludeCredentials?.map((item) => ({
      ...item,
      id: decodeBase64url(item.id),
    })),
  } as PublicKeyCredentialCreationOptions;
}

export function serializeCredential(credential: PublicKeyCredential): Record<string, unknown> {
  const response = credential.response;
  const common = { clientDataJSON: encodeBase64url(response.clientDataJSON) };
  if (response instanceof AuthenticatorAssertionResponse) {
    return {
      id: credential.id,
      rawId: encodeBase64url(credential.rawId),
      type: credential.type,
      response: {
        ...common,
        authenticatorData: encodeBase64url(response.authenticatorData),
        signature: encodeBase64url(response.signature),
        userHandle: encodeBase64url(response.userHandle),
      },
    };
  }
  const attestation = response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: encodeBase64url(credential.rawId),
    type: credential.type,
    response: {
      ...common,
      attestationObject: encodeBase64url(attestation.attestationObject),
      transports: attestation.getTransports?.() || [],
    },
  };
}
