import { useState } from 'react'
import { Link } from 'react-router-dom'
import { SignOut } from '@phosphor-icons/react'
import { Avatar, Button, Icon, Modal } from '../ui'
import { useAuth } from '../../features/auth/AuthContext'
import styles from './UserMenu.module.css'

/**
 * Topbar profile control: a pill (avatar + name → /cuenta) plus a logout
 * button that asks for confirmation in a glass modal before signing out.
 */
export function UserMenu() {
  const { user, signOut } = useAuth()
  const [confirmOpen, setConfirmOpen] = useState(false)

  if (!user) return null

  const confirmSignOut = () => {
    setConfirmOpen(false)
    void signOut()
  }

  return (
    <>
      <div className={styles.wrap}>
        <Link className={styles.profile} to="/cuenta" title="Ver cuenta">
          <Avatar name={user.name} />
          <span className={styles.name}>{user.name}</span>
        </Link>
        <button
          type="button"
          className={styles.logout}
          onClick={() => setConfirmOpen(true)}
          aria-label="Cerrar sesión"
          title="Cerrar sesión"
        >
          <Icon icon={SignOut} size="sm" />
        </button>
      </div>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        showClose={false}
        aria-label="Confirmar cierre de sesión"
      >
        <div className={styles.confirm}>
          <div className={styles.confirmIco}>
            <Icon icon={SignOut} size={22} />
          </div>
          <h3 className={styles.confirmTitle}>¿Cerrar sesión?</h3>
          <p className={styles.confirmText}>Volverás a la pantalla de inicio de sesión.</p>
          <div className={styles.confirmActions}>
            <Button variant="glass" onClick={() => setConfirmOpen(false)}>
              No
            </Button>
            <Button variant="glass" className={styles.danger} onClick={confirmSignOut}>
              Sí, cerrar sesión
            </Button>
          </div>
        </div>
      </Modal>
    </>
  )
}
