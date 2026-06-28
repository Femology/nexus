/**
 * Key Vault Service
 *
 * Wraps VS Code's native SecretStorage API (backed by the OS credential
 * store: Keychain on macOS, libsecret on Linux, Windows Credential Manager
 * on Windows).
 *
 * HARD CONSTRAINTS (Separation of Concerns / Security):
 *  - Never assign a raw key to a class property or module-level variable.
 *  - The getKey() return value must be used immediately by the caller and
 *    never cached longer than a single request.
 *  - Never log a key value.
 *
 * This is the ONLY service permitted to touch API key material.
 */

import * as vscode from 'vscode';

/** Prefix used to namespace per-alias key entries in SecretStorage. */
const KEY_PREFIX = 'nexuscode-key-';

/** SecretStorage key under which the JSON array of alias strings lives. */
const ALIAS_LIST_KEY = 'nexuscode-key-aliases';

/** Metadata describing a registered key alias (no key material). */
export interface KeyAliasMeta {
  /** The alias name chosen by the user. */
  alias: string;
  /** The provider this alias is associated with (e.g., "openai"). */
  provider: string;
}

export class KeyVault {
  private readonly secrets: vscode.SecretStorage;

  public constructor(secretStorage: vscode.SecretStorage) {
    this.secrets = secretStorage;
  }

  /**
   * Store an API key under the given alias.
   *
   * The provider string is stored separately as informational metadata so
   * the settings UI can display which provider each alias belongs to. The
   * key value itself is never recorded in the metadata.
   */
  public async storeKey(alias: string, key: string, provider: string): Promise<void> {
    if (!alias) {
      throw new Error('KeyVault.storeKey: alias must be a non-empty string');
    }
    if (!key) {
      throw new Error('KeyVault.storeKey: key must be a non-empty string');
    }

    await this.secrets.store(KEY_PREFIX + alias, key);
    await this.registerAlias(alias, provider);
  }

  /**
   * Retrieve the raw API key for an alias.
   *
   * IMPORTANT: The caller must use this value immediately (inject into an
   * Authorization header) and must not store it anywhere. This method does
   * not cache the value.
   *
   * @returns The raw key, or undefined if the alias is unknown.
   */
  public async getKey(alias: string): Promise<string | undefined> {
    if (!alias) {
      return undefined;
    }
    return this.secrets.get(KEY_PREFIX + alias);
  }

  /**
   * Delete the key and metadata for an alias.
   */
  public async deleteKey(alias: string): Promise<void> {
    await this.secrets.delete(KEY_PREFIX + alias);
    await this.unregisterAlias(alias);
  }

  /**
   * Rotate (overwrite) an existing alias's key with a new value.
   *
   * Because SecretStorage.store overwrites atomically, there is never a
   * window during which both old and new keys coexist in the clear.
   */
  public async rotateKey(alias: string, newKey: string): Promise<void> {
    if (!newKey) {
      throw new Error('KeyVault.rotateKey: newKey must be a non-empty string');
    }
    await this.secrets.store(KEY_PREFIX + alias, newKey);
  }

  /**
   * List all registered key aliases (no key material returned).
   */
  public async listAliases(): Promise<KeyAliasMeta[]> {
    const raw = await this.secrets.get(ALIAS_LIST_KEY);
    if (!raw) {
      return [];
    }
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed
        .filter((item): item is KeyAliasMeta => {
          return (
            typeof item === 'object' &&
            item !== null &&
            typeof (item as KeyAliasMeta).alias === 'string'
          );
        })
        .map((item) => ({
          alias: item.alias,
          provider: typeof item.provider === 'string' ? item.provider : 'unknown',
        }));
    } catch {
      return [];
    }
  }

  // ---------------------------------------------------------------------------
  // Private alias-list metadata maintenance
  // ---------------------------------------------------------------------------

  private async registerAlias(alias: string, provider: string): Promise<void> {
    const aliases = await this.listAliases();
    const existingIndex = aliases.findIndex((a) => a.alias === alias);
    if (existingIndex >= 0) {
      aliases[existingIndex] = { alias, provider };
    } else {
      aliases.push({ alias, provider });
    }
    await this.secrets.store(ALIAS_LIST_KEY, JSON.stringify(aliases));
  }

  private async unregisterAlias(alias: string): Promise<void> {
    const aliases = await this.listAliases();
    const filtered = aliases.filter((a) => a.alias !== alias);
    await this.secrets.store(ALIAS_LIST_KEY, JSON.stringify(filtered));
  }
}
