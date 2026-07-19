const SUPERUSER_COUNT_PATTERN = /(?:^|\r?\n)__E2E_SUPERUSER_COUNT__=(\d+)(?:\r?\n|$)/;


export function parseSuperuserCount(output) {
  const match = output.match(SUPERUSER_COUNT_PATTERN);
  return match ? Number.parseInt(match[1], 10) : null;
}
