export function isSelectedResourceStarting(selectedId: string, startingId: string): boolean {
  return Boolean(selectedId) && selectedId === startingId
}

export const isSelectedServerStarting = isSelectedResourceStarting
