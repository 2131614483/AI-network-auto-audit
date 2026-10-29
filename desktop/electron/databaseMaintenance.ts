/** Explicit, fail-closed maintenance orchestration. */
export type DatabaseState =
  | { state: "new"; target: string }
  | { state: "ready"; current: string; target: string }
  | { state: "upgrade"; current: string; target: string }
  | { state: "blocked"; reason: string };

export type MaintenanceAction =
  | { kind: "initialize"; target: string }
  | { kind: "upgrade"; current: string; target: string };

export type DatabaseOperations = {
  inspect(): Promise<DatabaseState>;
  requestAction(action: MaintenanceAction): Promise<boolean>;
  initialize(target: string): Promise<void>;
  backup(): Promise<string>;
  verifyBackup(path: string): Promise<boolean>;
  migrate(target: string): Promise<void>;
};

export async function prepareDatabase(ops: DatabaseOperations): Promise<void> {
  const state = await ops.inspect();
  if (state.state === "blocked") throw new Error(state.reason);
  if (state.state === "ready") return;
  const action: MaintenanceAction = state.state === "new"
    ? { kind: "initialize", target: state.target }
    : { kind: "upgrade", current: state.current, target: state.target };
  if (!await ops.requestAction(action)) throw new Error("database maintenance cancelled");
  if (action.kind === "initialize") {
    await ops.initialize(action.target);
    return;
  }
  const backup = await ops.backup();
  if (!await ops.verifyBackup(backup)) throw new Error(`database backup verification failed: ${backup}`);
  await ops.migrate(action.target);
}
