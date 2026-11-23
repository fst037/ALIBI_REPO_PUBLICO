import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Camera,
  CreditCard,
  WarningCircle,
  DownloadSimple,
  Envelope,
  Lightning,
  Lock,
  LockKey,
  LockSimple,
  Moon,
  Receipt,
  SlidersHorizontal,
  Sun,
  Trash,
  User as UserIcon,
  Warning,
  WarningOctagon,
  X,
} from '@phosphor-icons/react'
import type { Icon as PhosphorIcon } from '@phosphor-icons/react'
import { AppShell } from '../components/layout/AppShell'
import { Button, EmptyState, Field, Icon, PlanCard, Segmented, Switch, UsageBar } from '../components/ui'
import * as api from '../lib/api'
import type { BillingCycle, Plan, PlanId, Subscription, UsageMetric } from '../types'
import { cx } from '../lib/cx'
import { formatArs, formatUsd } from '../lib/format'
import { LegalLink } from '../features/legal/LegalLink'
import { priceRuleNote } from '../features/billing/priceNote'
import styles from './Cuenta.module.css'
import billing from './Suscripcion.module.css'

type Tab = 'perfil' | 'seg' | 'pref' | 'billing' | 'danger'
const TABS: { key: Exclude<Tab, 'danger'>; label: string; icon: PhosphorIcon }[] = [
  { key: 'perfil', label: 'Perfil', icon: UserIcon },
  { key: 'seg', label: 'Seguridad', icon: LockKey },
  { key: 'pref', label: 'Preferencias', icon: SlidersHorizontal },
  { key: 'billing', label: 'Suscripción y facturación', icon: CreditCard },
]

const PLAN_LABEL: Record<PlanId, string> = { free: 'Free', pro: 'Pro' }
const BILLING_OPTIONS: { value: BillingCycle; label: string }[] = [
  { value: 'monthly', label: 'Mensual' },
  { value: 'annual', label: 'Anual · −20%' },
]

type ModalView = 'closed' | 'form'

interface ChannelPref {
  key: string
  label: string
  app: boolean
  mail: boolean
}
const NOTIF_PREFS: ChannelPref[] = [
  { key: 'procesado', label: 'Archivo procesado', app: true, mail: true },
  { key: 'limite', label: 'Cerca del límite del plan', app: true, mail: true },
  { key: 'errores', label: 'Errores de procesamiento', app: true, mail: false },
  { key: 'producto', label: 'Novedades del producto', app: false, mail: true },
]

const initials = (name: string) => name.split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase()

function UsageBox({ metric }: { metric: UsageMetric }) {
  return (
    <div className={billing.ubox}>
      <div className={billing.uboxTop}>
        <span className={billing.uboxV}>{metric.used.toLocaleString('es-AR')}</span>
        <span className={billing.uboxMax}>{metric.limit != null ? `/ ${metric.limit.toLocaleString('es-AR')}` : 'sin límite'}</span>
      </div>
      <UsageBar value={metric.used} max={metric.limit ?? metric.used} />
      <div className={billing.uboxL}>{metric.label}</div>
    </div>
  )
}

const isTab = (v: string | null): v is Tab =>
  v === 'perfil' || v === 'seg' || v === 'pref' || v === 'billing' || v === 'danger'

export function Cuenta() {
  const [params] = useSearchParams()
  const [tab, setTab] = useState<Tab>(() => {
    const t = params.get('tab')
    return isTab(t) ? t : 'perfil'
  })

  // Profile
  // Empty until `/auth/me` resolves — these used to be seeded with the fixture
  // user's name, so every account flashed "Ana Ruiz" before showing its own.
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [company, setCompany] = useState('Southern Trading Ltd.')
  const [twofa, setTwofa] = useState(false)
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')
  const [mkt, setMkt] = useState(true)
  const [notifPrefs, setNotifPrefs] = useState<ChannelPref[]>(NOTIF_PREFS)
  const [saving, setSaving] = useState(false)

  // Subscription & billing
  const [sub, setSub] = useState<Subscription | null>(null)
  const [plans, setPlans] = useState<Plan[]>([])
  const [modal, setModal] = useState<ModalView>('closed')
  const [modalPlan, setModalPlan] = useState<Plan | null>(null)
  const [cycle, setCycle] = useState<BillingCycle>('annual')
  const [busy, setBusy] = useState(false)
  const [payErr, setPayErr] = useState(false)
  // Overage (Pro): the form's own copy, so typing a cap doesn't touch `sub`
  // until it is saved.
  const [overageOn, setOverageOn] = useState(false)
  const [capInput, setCapInput] = useState('')
  const [overageBusy, setOverageBusy] = useState(false)
  const [overageErr, setOverageErr] = useState<string | null>(null)
  // Cancelling Pro (thesis: "baja en cualquier momento, por el mismo medio de
  // contratación") — asked once inline, then the account moves to Free.
  const [cancelAsk, setCancelAsk] = useState(false)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelErr, setCancelErr] = useState<string | null>(null)

  const patchNotifPref = (i: number, patch: Partial<ChannelPref>) =>
    setNotifPrefs((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : p)))

  useEffect(() => {
    let alive = true
    void api.getCurrentUser().then((u) => {
      if (!alive) return
      const [first, ...rest] = u.name.split(' ')
      setFirstName(first)
      setLastName(rest.join(' '))
      setEmail(u.email)
    })
    void api.getSubscription().then((s) => alive && setSub(s))
    void api.listPlans().then((p) => alive && setPlans(p))
    return () => {
      alive = false
    }
  }, [])

  const saveProfile = async () => {
    setSaving(true)
    try {
      await api.updateProfile({ name: `${firstName} ${lastName}`.trim(), email })
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    if (!sub) return
    setOverageOn(sub.overage.enabled)
    setCapInput(sub.overage.capArs != null ? String(sub.overage.capArs) : '')
  }, [sub])

  const paid = plans.filter((p) => p.id !== 'free')
  const currentPlan = plans.find((p) => p.id === sub?.planId)
  // The paid plan whose prices the footer explains: the one you are on, or Pro.
  const pricedPlan = currentPlan && currentPlan.id !== 'free' ? currentPlan : paid[0]
  const capValue = capInput.trim() === '' ? null : Number(capInput)
  const capInvalid = capValue != null && (!Number.isFinite(capValue) || capValue <= 0)
  const overageDirty =
    !!sub && (overageOn !== sub.overage.enabled || capValue !== sub.overage.capArs)
  const saveOverage = async () => {
    if (capInvalid || overageBusy) return
    setOverageBusy(true)
    setOverageErr(null)
    try {
      setSub(await api.setOverage(overageOn, capValue))
    } catch {
      setOverageErr('No pudimos guardar el cambio. Probá de nuevo.')
    } finally {
      setOverageBusy(false)
    }
  }
  const cancelSubscription = async () => {
    if (cancelBusy) return
    setCancelBusy(true)
    setCancelErr(null)
    try {
      setSub(await api.upgradePlan('free', 'monthly'))
      setCancelAsk(false)
    } catch {
      setCancelErr('No pudimos dar de baja la suscripción. Probá de nuevo o escribinos.')
    } finally {
      setCancelBusy(false)
    }
  }
  const openUpgrade = (plan: Plan) => {
    setModalPlan(plan)
    setModal('form')
  }
  const confirmUpgrade = async () => {
    if (!modalPlan || busy) return
    setBusy(true)
    setPayErr(false)
    try {
      // The card is entered on Mercado Pago's page; it sends the user back to
      // /pago/resultado, which verifies the payment and activates the plan.
      const { checkoutUrl } = await api.startCheckout(modalPlan.id, cycle, 'cuenta')
      window.location.assign(checkoutUrl)
    } catch {
      setPayErr(true)
      setBusy(false)
    }
  }

  const annual = cycle === 'annual'
  const base = modalPlan ? api.priceFor(modalPlan, cycle) : 0
  const amount = annual ? base * 12 : base
  const tax = Math.round(amount * 0.21)
  const total = amount + tax

  return (
    <AppShell title="Cuenta" activeNav="cuenta">
      <div className="page-scroll">
        <div className="page-pad">
          <div className="page-head">
            <h1 className="page-h">Tu cuenta</h1>
            <p className="page-sub">Administrá tus datos personales, seguridad, preferencias y plan.</p>
          </div>

          <div className={styles.layout}>
            <div className={styles.tabs}>
              {TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={cx(styles.tab, tab === t.key && styles.active)}
                  onClick={() => setTab(t.key)}
                >
                  <Icon icon={t.icon} />{t.label}
                </button>
              ))}
              <button
                type="button"
                className={cx(styles.tab, styles.danger, tab === 'danger' && styles.active)}
                onClick={() => setTab('danger')}
              >
                <Icon icon={Warning} />Eliminar cuenta
              </button>
            </div>

            <div>
              {tab === 'perfil' && (
                <div className={cx('card', styles.secCard)}>
                  <div className="row" style={{ gap: 16, marginBottom: 22 }}>
                    <div className={styles.avatarBig}>{initials(`${firstName} ${lastName}`)}</div>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 15 }}>{firstName} {lastName}</div>
                      <div className="hint" style={{ marginTop: 3 }}>Miembro desde marzo de 2026</div>
                      <Button variant="glass" size="sm" style={{ marginTop: 10 }}><Icon icon={Camera} size="sm" />Cambiar foto</Button>
                    </div>
                  </div>
                  <div className={styles.row2}>
                    <Field label="Nombre" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
                    <Field label="Apellido" value={lastName} onChange={(e) => setLastName(e.target.value)} />
                  </div>
                  <div style={{ marginTop: 16 }}><Field label="Email" icon={Envelope} value={email} onChange={(e) => setEmail(e.target.value)} /></div>
                  <div style={{ marginTop: 16 }}><Field label="Empresa (opcional)" value={company} onChange={(e) => setCompany(e.target.value)} /></div>
                  <div className="row" style={{ justifyContent: 'flex-end', marginTop: 20, gap: 10 }}>
                    <Button variant="ghost">Cancelar</Button>
                    <Button variant="primary" onClick={saveProfile} disabled={saving}>Guardar cambios</Button>
                  </div>
                </div>
              )}

              {tab === 'seg' && (
                <>
                  <div className={cx('card', styles.secCard)}>
                    <div className={styles.secCardH}><h3 className="card-title">Cambiar contraseña</h3><p className="card-sub">Usá al menos 8 caracteres, con mayúsculas y números.</p></div>
                    <div style={{ marginBottom: 16 }}><Field label="Contraseña actual" icon={Lock} type="password" /></div>
                    <div className={styles.row2}>
                      <Field label="Contraseña nueva" icon={LockKey} type="password" />
                      <Field label="Repetir contraseña nueva" icon={LockKey} type="password" />
                    </div>
                    <div className="row" style={{ justifyContent: 'flex-end', marginTop: 20 }}>
                      <Button variant="primary">Actualizar contraseña</Button>
                    </div>
                  </div>
                  <div className={cx('card', styles.secCard)}>
                    <div className="between">
                      <div><h3 className="card-title">Verificación en dos pasos</h3><p className="card-sub">Sumá una capa extra de seguridad al iniciar sesión.</p></div>
                      <Switch checked={twofa} onChange={setTwofa} label="Verificación en dos pasos" />
                    </div>
                  </div>
                </>
              )}

              {tab === 'pref' && (
                <>
                <div className={cx('card', styles.secCard)}>
                  <div className={styles.secCardH}><h3 className="card-title">Preferencias</h3></div>
                  <div className={styles.prefRow}>
                    <div><div style={{ fontWeight: 500, fontSize: 14 }}>Tema</div><div className="hint" style={{ marginTop: 2 }}>Apariencia de la aplicación</div></div>
                    <div className={styles.themeOpt}>
                      <button type="button" className={cx(styles.tchip, theme === 'dark' && styles.on)} onClick={() => setTheme('dark')}><Icon icon={Moon} size="sm" />Oscuro</button>
                      <button type="button" className={cx(styles.tchip, theme === 'light' && styles.on)} onClick={() => setTheme('light')}><Icon icon={Sun} size="sm" />Claro</button>
                    </div>
                  </div>
                  <div className={styles.prefRow}>
                    <div><div style={{ fontWeight: 500, fontSize: 14 }}>Formato de números</div><div className="hint" style={{ marginTop: 2 }}>Separadores de miles y decimales</div></div>
                    <select className="field" style={{ width: 200 }}><option>1.234,56 (coma decimal)</option><option>1,234.56 (punto decimal)</option></select>
                  </div>
                  <div className={styles.prefRow}>
                    <div><div style={{ fontWeight: 500, fontSize: 14 }}>Emails del producto</div><div className="hint" style={{ marginTop: 2 }}>Novedades y consejos ocasionales</div></div>
                    <Switch checked={mkt} onChange={setMkt} label="Emails del producto" />
                  </div>
                </div>
                <div className={cx('card', styles.secCard)}>
                  <div className={styles.secCardH}><h3 className="card-title">Notificaciones</h3><p className="card-sub">Elegí cómo querés que te avisemos.</p></div>
                  <div className={styles.chanHead}><div /><div className={styles.chanLab}>App</div><div className={styles.chanLab}>Email</div></div>
                  {notifPrefs.map((p, i) => (
                    <div key={p.key} className={styles.chanRow}>
                      <div style={{ fontSize: 13, color: 'var(--fg-1)' }}>{p.label}</div>
                      <div className={styles.chanCheck}><Switch checked={p.app} onChange={(v) => patchNotifPref(i, { app: v })} label={`${p.label} en la app`} /></div>
                      <div className={styles.chanCheck}><Switch checked={p.mail} onChange={(v) => patchNotifPref(i, { mail: v })} label={`${p.label} por email`} /></div>
                    </div>
                  ))}
                </div>
                </>
              )}

              {tab === 'billing' && (
                <>
                  {sub && (
                    <div className={`card ${billing.secBlock}`}>
                      <div className={billing.planNow}>
                        <div>
                          <div className="row" style={{ gap: 10 }}>
                            <span className="pill">Plan actual</span>
                            <h3 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>{PLAN_LABEL[sub.planId]}</h3>
                          </div>
                          <p className="hint" style={{ margin: '8px 0 0' }}>
                            {currentPlan && currentPlan.priceMonthly > 0
                              ? `${formatArs(api.priceFor(currentPlan, sub.cycle))} por mes + IVA (${formatUsd(sub.cycle === 'annual' ? currentPlan.priceAnnualUsd : currentPlan.priceMonthlyUsd)} al dólar oficial) · ${sub.cycle === 'annual' ? 'pago anual por adelantado' : 'pago mensual'}`
                              : 'Sin costo · sin fecha de renovación'}
                          </p>
                        </div>
                        {sub.planId === 'free' && paid[0] && (
                          <Button variant="primary" onClick={() => openUpgrade(paid[0])}><Icon icon={Lightning} size="sm" />Pasar a Pro</Button>
                        )}
                        {sub.planId !== 'free' && !cancelAsk && (
                          <Button variant="ghost" onClick={() => setCancelAsk(true)}>Dar de baja la suscripción</Button>
                        )}
                      </div>
                      {sub.planId !== 'free' && cancelAsk && (
                        <div className={billing.cancelBox}>
                          <div style={{ fontWeight: 600, fontSize: 13.5, color: 'var(--fg-1)' }}>¿Dar de baja {PLAN_LABEL[sub.planId]}?</div>
                          <p className="hint" style={{ margin: '6px 0 12px', lineHeight: 1.55 }}>
                            Tu cuenta pasa al plan Free y no se generan nuevos cargos, salvo las consultas adicionales que ya
                            usaste este mes. Tus datos no se borran, pero quedan sujetos a los límites de Free (3 datasets,
                            20 consultas por mes, historial de 7 días).
                          </p>
                          <div className="row" style={{ gap: 10 }}>
                            <Button variant="glass" onClick={() => setCancelAsk(false)} disabled={cancelBusy}>Mantener {PLAN_LABEL[sub.planId]}</Button>
                            <Button variant="primary" onClick={cancelSubscription} disabled={cancelBusy} style={{ background: 'var(--danger)', borderColor: 'var(--danger)' }}>
                              Confirmar la baja
                            </Button>
                          </div>
                          {cancelErr && <div className="field-msg" style={{ marginTop: 10 }}>{cancelErr}</div>}
                        </div>
                      )}
                      <div className={billing.usageGrid}>
                        <UsageBox metric={sub.usage.queries} />
                        <UsageBox metric={sub.usage.datasets} />
                        <UsageBox metric={sub.usage.rowsPerQuery} />
                      </div>
                    </div>
                  )}

                  {sub?.overage.available && (
                    <div className={billing.secBlock}>
                      <h3 className={billing.secBlockH}>Consultas adicionales</h3>
                      <div className="card">
                        <p className="hint" style={{ margin: '0 0 16px', lineHeight: 1.55 }}>
                          Cuando usás las {sub.usage.queries.limit?.toLocaleString('es-AR')} consultas del mes, podés seguir preguntando a{' '}
                          <span className="mono" style={{ color: 'var(--fg-1)' }}>{formatArs(sub.overage.pricePerQueryArs ?? 0)}</span> + IVA por consulta
                          {currentPlan?.overagePricePerQueryUsd != null && <> ({formatUsd(currentPlan.overagePricePerQueryUsd)} al dólar oficial)</>}, sin cambiar de plan.
                          Se cobran a mes vencido y nunca por encima del tope que fijes. Te avisamos al llegar al 80 % de tu cupo.
                        </p>
                        <Switch checked={overageOn} onChange={setOverageOn} label="Activar consultas adicionales" showLabel />
                        {overageOn && (
                          <div className={billing.overageCap}>
                            <Field
                              label="Tope de gasto por mes (ARS, sin IVA)"
                              placeholder="Sin tope"
                              inputMode="numeric"
                              value={capInput}
                              onChange={(e) => setCapInput(e.target.value.replace(/[^\d]/g, ''))}
                              error={capInvalid ? 'Ingresá un monto mayor a cero, o dejalo vacío para no tener tope' : undefined}
                            />
                            {capValue != null && !capInvalid && sub.overage.pricePerQueryArs ? (
                              <div className="hint">
                                Hasta {Math.floor(capValue / sub.overage.pricePerQueryArs).toLocaleString('es-AR')} consultas adicionales por mes.
                              </div>
                            ) : null}
                          </div>
                        )}
                        <div className={billing.overageMonth}>
                          <div>
                            <div className={billing.uboxL} style={{ marginTop: 0 }}>Este mes</div>
                            <div className="mono" style={{ fontSize: 15, color: 'var(--fg-1)', marginTop: 4 }}>
                              {sub.overage.queries.toLocaleString('es-AR')} consultas adicionales · {formatArs(sub.overage.amountArs)}
                            </div>
                          </div>
                          <Button variant="primary" onClick={saveOverage} disabled={!overageDirty || capInvalid || overageBusy}>
                            Guardar
                          </Button>
                        </div>
                        {sub.overage.enabled && sub.overage.capArs != null && (
                          <div style={{ marginTop: 12 }}>
                            <UsageBar value={sub.overage.amountArs} max={sub.overage.capArs} />
                          </div>
                        )}
                        {overageErr && <div className="field-msg" style={{ marginTop: 10 }}>{overageErr}</div>}
                      </div>
                    </div>
                  )}

                  {sub?.planId === 'free' && (
                    <div className={billing.secBlock}>
                      <h3 className={billing.secBlockH}>Mejorá tu plan</h3>
                      <div className={billing.upPlans}>
                        {paid.map((p) => (
                          <PlanCard
                            key={p.id}
                            name={PLAN_LABEL[p.id]}
                            featured={p.recommended}
                            badge={p.recommended ? 'Recomendado' : undefined}
                            price={formatArs(p.priceMonthly)}
                            per="/mes + IVA"
                            tagline={`O ${formatArs(p.priceAnnual)} por mes con pago anual.`}
                            features={p.features}
                            cta={
                              <Button variant={p.recommended ? 'primary' : 'glass'} onClick={() => openUpgrade(p)} style={{ justifyContent: 'center', marginTop: 'auto' }}>
                                {p.recommended && <Icon icon={Lightning} size="sm" />}Elegir {PLAN_LABEL[p.id]}
                              </Button>
                            }
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  <div className={billing.secBlock}>
                    <h3 className={billing.secBlockH}>Método de pago</h3>
                    <div className="card">
                      {sub?.paymentMethod ? (
                        <div className="row" style={{ gap: 12 }}>
                          <Icon icon={CreditCard} size={22} />
                          <span>{sub.paymentMethod.brand} •••• {sub.paymentMethod.last4}</span>
                          <span className="hint">vence {sub.paymentMethod.expiry}</span>
                        </div>
                      ) : (
                        <EmptyState icon={CreditCard} title="Sin método de pago" description="Agregá una tarjeta cuando pases a un plan pago." />
                      )}
                    </div>
                  </div>

                  <div className={billing.secBlock}>
                    <h3 className={billing.secBlockH}>Facturas</h3>
                    <div className="card">
                      {sub && sub.invoices.length > 0 ? (
                        sub.invoices.map((inv) => (
                          <div key={inv.id} className="between" style={{ padding: '8px 0' }}>
                            <span className="mono">{inv.date}</span>
                            <span className="mono">{formatArs(inv.amount)}</span>
                          </div>
                        ))
                      ) : (
                        <EmptyState icon={Receipt} title="Todavía no hay facturas" description="Tus facturas electrónicas van a aparecer acá cuando estés en un plan pago." />
                      )}
                    </div>
                  </div>

                  <div className="hint" style={{ textAlign: 'center', marginTop: 8 }}>
                    {pricedPlan ? priceRuleNote(pricedPlan) : 'Precios en pesos argentinos, más IVA.'}{' '}
                    ¿Necesitás ayuda con la facturación? <Link to="/ayuda" style={{ color: 'var(--accent)' }}>Contactá a soporte</Link>.
                    {' '}Condiciones completas en los <LegalLink doc="terms" section="planes">Términos y condiciones</LegalLink>.
                  </div>
                </>
              )}

              {tab === 'danger' && (
                <div className={cx('card', styles.secCard, styles.dangerCard)}>
                  <div className={styles.secCardH}><h3 className="card-title" style={{ color: 'var(--danger)' }}>Eliminar cuenta</h3><p className="card-sub">Se eliminan tus datasets, conversaciones e historial. Tenés 30 días para exportarlos; después la eliminación es definitiva.</p></div>
                  <div className={styles.dangerNote}>
                    <Icon icon={WarningOctagon} weight="fill" size={20} color="var(--danger)" style={{ flex: 'none', marginTop: 1 }} />
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--fg-1)' }}>Antes de irte</div>
                      <p style={{ margin: '5px 0 0', color: 'var(--fg-2)', fontSize: 12.5, lineHeight: 1.5 }}>Descargá tus datos si los necesitás. Vencidos los 30 días no hay forma de recuperarlos. Más detalles en la <LegalLink doc="privacy" section="conservacion">Política de privacidad</LegalLink>.</p>
                    </div>
                  </div>
                  <div className="row" style={{ gap: 10 }}>
                    <Button variant="glass"><Icon icon={DownloadSimple} size="sm" />Exportar mis datos</Button>
                    <Button variant="primary" style={{ background: 'var(--danger)', borderColor: 'var(--danger)' }}><Icon icon={Trash} size="sm" />Eliminar mi cuenta</Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {modal !== 'closed' && modalPlan && (
        <div className={billing.scrim} onClick={() => setModal('closed')}>
          <div className={billing.upmodal} onClick={(e) => e.stopPropagation()}>
            {modal === 'form' && (
              <>
                <div className={billing.upmodalForm}>
                  <div className="between" style={{ marginBottom: 20 }}>
                    <div>
                      <h3 style={{ fontSize: 17, fontWeight: 600, margin: 0 }}>Pasar a {PLAN_LABEL[modalPlan.id]}</h3>
                      <p className="hint" style={{ margin: '4px 0 0' }}>Cancelás cuando quieras.</p>
                    </div>
                    <Button variant="ghost" iconOnly onClick={() => setModal('closed')} aria-label="Cerrar"><Icon icon={X} size="sm" /></Button>
                  </div>
                  <div style={{ marginBottom: 18 }}>
                    <Segmented options={BILLING_OPTIONS} value={cycle} onChange={setCycle} aria-label="Ciclo de facturación" />
                  </div>
                  <div className="hint" style={{ display: 'flex', alignItems: 'flex-start', gap: 7 }}>
                    <Icon icon={LockSimple} size="sm" />
                    Vas a completar el pago en la página segura de Mercado Pago. ALIBI nunca ve ni guarda los datos de tu tarjeta.
                  </div>
                  <div className="hint" style={{ marginTop: 14 }}>
                    Mientras usemos credenciales de prueba, el pago es una simulación: usá una tarjeta de prueba de Mercado Pago y no se cobra dinero real.
                  </div>
                  {payErr && (
                    <div className="field-msg" style={{ marginTop: 14 }}>
                      <Icon icon={WarningCircle} size="sm" />No pudimos iniciar el pago. Probá de nuevo en unos minutos.
                    </div>
                  )}
                </div>
                <div className={billing.upmodalSummary}>
                  <div className="sb-lab" style={{ padding: '0 0 14px' }}>Resumen</div>
                  <div className={billing.sumLine}><span>{PLAN_LABEL[modalPlan.id]} ({annual ? '12 meses' : 'mensual'})</span><span className="mono">{formatArs(amount)}</span></div>
                  {annual && <div className={billing.sumLine} style={{ color: 'var(--accent)' }}><span>Incluye descuento anual</span><span className="mono">−20%</span></div>}
                  <div className={billing.sumLine}><span>IVA (21%)</span><span className="mono">{formatArs(tax)}</span></div>
                  <div className={billing.sumTotal}><span>Total {annual ? 'anual' : 'mensual'}</span><span className="mono">{formatArs(total)}</span></div>
                  <div className="grow" />
                  <Button variant="primary" onClick={confirmUpgrade} disabled={busy} style={{ justifyContent: 'center', marginTop: 20, width: '100%' }}>
                    <Icon icon={LockSimple} size="sm" />Pagar con Mercado Pago
                  </Button>
                  <div className="hint" style={{ textAlign: 'center', marginTop: 10 }}>
                    {annual
                      ? 'Se cobra por adelantado, al precio en pesos de hoy, y lo mantiene 12 meses.'
                      : 'Se renueva automáticamente; cada mes se factura al dólar oficial del día. Cancelás cuando quieras.'}{' '}
                    Factura electrónica. Al confirmar aceptás los <LegalLink doc="terms" section="planes">Términos</LegalLink>.
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </AppShell>
  )
}
