export type CleanupCallback = () => Promise<void>;

type CleanupEntry = {
  label: string;
  callback: CleanupCallback;
};

export type CleanupRegistry = {
  add(label: string, callback: CleanupCallback): void;
  run(): Promise<void>;
  readonly registered: number;
};

export function createCleanupRegistry(): CleanupRegistry {
  const entries: CleanupEntry[] = [];
  return {
    add(label, callback) {
      if (!label.trim()) throw new Error('Cleanup labels must not be empty.');
      entries.push({ label, callback });
    },
    async run() {
      const failures: string[] = [];
      for (const entry of [...entries].reverse()) {
        try {
          await entry.callback();
        } catch (error) {
          failures.push(`${entry.label}: ${String(error)}`);
        }
      }
      entries.length = 0;
      if (failures.length > 0) {
        throw new Error(`E2E cleanup failures:\n${failures.map((failure) => `- ${failure}`).join('\n')}`);
      }
    },
    get registered() {
      return entries.length;
    },
  };
}
