import { useEffect, useState } from 'react'

/**
 * Countdown for the "Reenviar código" link. The backend accepts one code per
 * email per minute (`resend_too_soon` otherwise), so the link stays disabled
 * and shows the wait instead of letting a click silently bounce off a 429.
 */
export function useResendCooldown(seconds = 60) {
  const [left, setLeft] = useState(0)

  useEffect(() => {
    if (left <= 0) return
    const timer = setTimeout(() => setLeft((s) => s - 1), 1000)
    return () => clearTimeout(timer)
  }, [left])

  return {
    /** Seconds until a new code can be requested; 0 = it can be now. */
    left,
    /** Call whenever a code has just been sent. */
    start: () => setLeft(seconds),
    /** "0:45" */
    label: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`,
  }
}
