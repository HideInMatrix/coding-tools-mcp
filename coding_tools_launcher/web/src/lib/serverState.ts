export function isSelectedServerStarting(selectedId: string, startingId: string): boolean {
  return Boolean(selectedId) && selectedId === startingId
}
