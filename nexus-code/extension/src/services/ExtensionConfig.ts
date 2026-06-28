/**
 * Extension Configuration Reader
 *
 * Centralizes reads of the `nexuscode.*` settings defined in package.json.
 * This is the single place that touches `vscode.workspace.getConfiguration`
 * for extension settings, keeping configuration access DRY.
 */

import * as vscode from 'vscode';

export interface NexusSettings {
  defaultModel: string;
  streamingEnabled: boolean;
  terminalContextEnabled: boolean;
  maxOpenTabsContext: number;
  heavyContextThreshold: number;
  daemonPort: number;
  daemonMaxRestarts: number;
}

const SECTION = 'nexuscode';

/** Read the full set of Nexus-Code settings. */
export function getSettings(): NexusSettings {
  const cfg = vscode.workspace.getConfiguration(SECTION);
  return {
    defaultModel: cfg.get<string>('defaultModel', 'gpt-4o'),
    streamingEnabled: cfg.get<boolean>('streamingEnabled', true),
    terminalContextEnabled: cfg.get<boolean>('terminalContextEnabled', true),
    maxOpenTabsContext: cfg.get<number>('maxOpenTabsContext', 10),
    heavyContextThreshold: cfg.get<number>('heavyContextThreshold', 8000),
    daemonPort: cfg.get<number>('daemonPort', 8000),
    daemonMaxRestarts: cfg.get<number>('daemonMaxRestarts', 5),
  };
}

/** Update a single setting value at the global (user) scope. */
export async function updateSetting(key: string, value: unknown): Promise<void> {
  const cfg = vscode.workspace.getConfiguration(SECTION);
  await cfg.update(key, value, vscode.ConfigurationTarget.Global);
}
