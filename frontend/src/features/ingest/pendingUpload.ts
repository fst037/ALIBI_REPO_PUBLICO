/* ============================================================
   Handoff for a file dropped somewhere other than the ingest screen.

   The dropzone on the source picker (`/app`) navigates to `/ingesta`, which is
   where the whole upload flow lives — the stepper, the schema confirmation, the
   retry. Before this box existed the navigation was all that happened: the
   `File` the user had already dropped was discarded and they had to drag it a
   second time onto an identical-looking dropzone.

   A `File` cannot travel in the URL, so it is parked here for exactly one read.
   Router state (`navigate(..., { state })`) would also carry it — `File` is
   structured-cloneable — but history state is *restored*, so a reload or a
   Back/Forward would replay an upload the user never asked for a second time.
   A module-level slot is deliberately the opposite: it does not survive a
   reload, and `takePendingUpload` empties it so the same drop can only ever
   start one upload.
   ============================================================ */

let pending: File | null = null

/** Park a dropped file for the ingest screen to pick up on arrival. */
export function setPendingUpload(file: File): void {
  pending = file
}

/** Whether a drop is waiting. Peeks — does not consume. */
export function hasPendingUpload(): boolean {
  return pending !== null
}

/** Take the parked file, emptying the slot. Returns null when there is none. */
export function takePendingUpload(): File | null {
  const file = pending
  pending = null
  return file
}
