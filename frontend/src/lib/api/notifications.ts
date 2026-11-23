import type { Notification } from '../../types'
import * as gen from './generated/notifications/notifications'
import { authInit, unwrap } from './http'

export function listNotifications(): Promise<Notification[]> {
  return unwrap<Notification[]>(gen.listNotifications(authInit()))
}

export async function unreadCount(): Promise<number> {
  const { count } = await unwrap<{ count: number }>(gen.getUnreadCount(authInit()))
  return count
}

export function markAsRead(id: string): Promise<Notification[]> {
  return unwrap<Notification[]>(gen.markNotificationRead(id, authInit()))
}

export function markAllRead(): Promise<Notification[]> {
  return unwrap<Notification[]>(gen.markAllNotificationsRead(authInit()))
}
