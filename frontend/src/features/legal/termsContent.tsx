import { AI_NOTICE, SUPPORT_EMAIL } from '../../lib/legal'
import { LegalCallout, type LegalDocContent, type LegalSection } from './legalParts'
import { LegalLink } from './LegalLink'

/*
 * Terms and Conditions. Their content follows the thesis (Capítulo 5,
 * "Viabilidad legal", Tabla "Contenido mínimo de los términos y condiciones",
 * and "Modelo de negocio" for plans, prices and billing). A clause that changes
 * here changes there too; bump TERMS_VERSION (lib/legal + backend/app/core/legal.py).
 */

const mail = <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>

const SECTIONS: LegalSection[] = [
  {
    id: 'objeto',
    title: 'Objeto y aceptación',
    body: (
      <>
        <p>
          Estos Términos y condiciones regulan el uso de ALIBI, una herramienta de análisis de datos asistida por
          inteligencia artificial que permite cargar archivos (CSV, Excel o PDF con tablas), hacer preguntas en lenguaje
          natural y obtener respuestas con la consulta, los datos y el gráfico que las sostienen.
        </p>
        <p>
          Al crear una cuenta, el usuario declara haber leído y aceptado estos Términos y la{' '}
          <LegalLink doc="privacy">Política de privacidad</LegalLink>, que forma parte de ellos. Si no está de acuerdo, no
          debe registrarse ni usar el servicio.
        </p>
      </>
    ),
  },
  {
    id: 'destinatarios',
    title: 'Destinatarios: uso profesional',
    body: (
      <>
        <p>
          ALIBI se ofrece para <strong>uso profesional</strong>: a empresas, en especial pequeñas y medianas, y a
          profesionales que lo integran a su actividad. Quien se registra en nombre de una organización declara contar
          con facultades para obligarla. El servicio no está destinado a menores de 18 años.
        </p>
        <p>
          ALIBI <strong>no brinda asesoramiento contable, financiero, impositivo ni legal</strong>.
        </p>
      </>
    ),
  },
  {
    id: 'cuenta',
    title: 'Cuenta y seguridad',
    body: (
      <p>
        El usuario debe proporcionar datos verdaderos y mantenerlos actualizados, y es responsable de la
        confidencialidad de su contraseña y de toda actividad realizada desde su cuenta. Debe avisar de inmediato a{' '}
        {mail} si sospecha un acceso no autorizado.
      </p>
    ),
  },
  {
    id: 'uso-aceptable',
    title: 'Uso aceptable y datos que se cargan',
    body: (
      <>
        <p>Al cargar un archivo, el usuario declara y garantiza que:</p>
        <ul>
          <li>
            tiene derecho a usar esos datos y cuenta con la base legal y, cuando corresponda, con el consentimiento de
            los titulares de los datos personales que contienen (por ejemplo, clientes o empleados), en los términos de
            la Ley 25.326;
          </li>
          <li>
            los archivos <strong>no contienen datos sensibles</strong> (los que revelan origen racial o étnico,
            opiniones políticas, convicciones religiosas, filosóficas o morales, afiliación sindical, información
            referente a la salud o a la vida sexual), cuya carga está prohibida;
          </li>
          <li>los datos no son ilícitos ni infringen derechos de terceros.</li>
        </ul>
        <p>
          Respecto de esos datos, el usuario conserva la condición de responsable frente a sus titulares y asume toda
          responsabilidad por su contenido y su licitud. ALIBI los trata solo por cuenta del usuario, como prestador de
          servicios informatizados (artículo 25 de la Ley 25.326): los usa exclusivamente para prestar el servicio, no
          los cede ni los aplica a otro fin y <strong>no los usa para entrenar modelos</strong>.
        </p>
        <p>
          Está prohibido intentar acceder a datos de otros usuarios, eludir los límites del plan o las protecciones del
          sistema, someterlo a cargas abusivas o usarlo para fines ilícitos. ALIBI puede suspender la cuenta que incumpla
          estas reglas.
        </p>
      </>
    ),
  },
  {
    id: 'resultados',
    title: 'Alcance de los resultados',
    body: (
      <>
        <p>
          Las respuestas se generan automáticamente con modelos de inteligencia artificial y pueden contener errores. El
          sistema reduce ese riesgo (las consultas son de solo lectura, cada respuesta se valida y se muestra la consulta
          que sostiene cada cifra), pero no puede eliminarlo. Junto a cada respuesta se muestra este aviso:
        </p>
        <LegalCallout>{AI_NOTICE}</LegalCallout>
        <p>
          Las decisiones que se adopten con base en los resultados son responsabilidad de quien las toma, que debe
          verificar la consulta que acompaña cada resultado.
        </p>
      </>
    ),
  },
  {
    id: 'propiedad',
    title: 'Propiedad intelectual',
    body: (
      <p>
        Los datos que el usuario carga y los resultados que obtiene (consultas, tablas y gráficos) le pertenecen. ALIBI
        solo recibe la autorización necesaria para procesarlos mientras dure el servicio. El software, la marca y los
        modelos de ALIBI son de sus titulares y están protegidos por la Ley 11.723; el usuario recibe una licencia de
        uso personal, no exclusiva e intransferible, limitada a la vigencia de su cuenta.
      </p>
    ),
  },
  {
    id: 'planes',
    title: 'Planes, precios y pagos',
    body: (
      <>
        <p>
          ALIBI ofrece un plan <strong>Free</strong>, sin cargo, y un plan <strong>Pro</strong> por suscripción, con
          pago mensual o anual anticipado (con descuento). Sobre el plan Pro, el usuario puede activar las{' '}
          <strong>consultas adicionales</strong>: superado el cupo mensual, cada consulta adicional se cobra por
          separado, nunca por encima del tope de gasto mensual que el propio usuario fija, y con un aviso al llegar al
          80 % del cupo. Están desactivadas hasta que el usuario las activa. Los límites de cada plan (consultas,
          conjuntos de datos, filas, tamaño de archivo, historial, formatos de exportación y modos de respuesta) se
          publican en la página de planes y en la sección Cuenta.
        </p>
        <p>
          Una <strong>consulta</strong> es una pregunta respondida. Las preguntas rechazadas por las protecciones del
          sistema o que fallan no se cuentan.
        </p>
        <p>
          <strong>Modos de respuesta.</strong> En modo <strong>Rápido</strong>, ALIBI resuelve cada pregunta en pocos
          pasos; en modo <strong>Profundo</strong>, la investiga en más pasos y redacta la respuesta con un modelo más
          capaz. El plan Free responde en modo Rápido; el plan Pro incluye ambos dentro de su cupo mensual, y las
          consultas adicionales se responden en modo Rápido. En los dos modos, cada pregunta respondida cuenta como una
          consulta y tiene el mismo precio.
        </p>
        <p>
          <strong>Precios en pesos atados al dólar.</strong> Los precios se publican, se muestran y se cobran en pesos
          argentinos, más IVA, con factura electrónica. Cada precio tiene asociado un valor de referencia en dólares
          estadounidenses, y el precio en pesos es ese valor multiplicado por la cotización oficial del dólar vigente;
          se recalcula cuando la cotización cambia. En consecuencia:
        </p>
        <ul>
          <li>la pantalla de planes y cada factura muestran el precio en pesos vigente;</li>
          <li>
            la suscripción mensual y las consultas adicionales, que se cobran a mes vencido, toman el precio en pesos del
            día en que se emite la factura;
          </li>
          <li>
            el plan anual se cobra por adelantado, al precio en pesos del día del pago, y ese precio se mantiene durante
            los doce meses pagados;
          </li>
          <li>
            el ajuste por cotización no es una modificación de precios y no requiere aviso. Un cambio del{' '}
            <strong>valor de referencia en dólares</strong> se avisa con <strong>treinta días</strong> de anticipación.
          </li>
        </ul>
        <p>
          El cobro se realiza mediante un procesador de pagos. ALIBI no almacena el número completo de la tarjeta: solo
          conserva la marca, los últimos cuatro dígitos y el vencimiento. La suscripción se renueva automáticamente al
          final de cada período, salvo que el usuario la dé de baja.
        </p>
      </>
    ),
  },
  {
    id: 'baja',
    title: 'Baja de la suscripción',
    body: (
      <>
        <p>
          El usuario puede dar de baja su suscripción <strong>en cualquier momento</strong>, por el mismo medio por el
          que la contrató: desde Cuenta → Suscripción y facturación. Desde la baja no se generan nuevos cargos, salvo
          las consultas adicionales ya consumidas en el mes, que se facturan a mes vencido. La cuenta pasa al plan Free.
        </p>
        <p>
          Si el usuario reviste la condición de consumidor, conserva los derechos que le reconoce la Ley 24.240, entre
          ellos el de revocar la contratación dentro de los diez días corridos.
        </p>
      </>
    ),
  },
  {
    id: 'conservacion',
    title: 'Conservación y eliminación de los datos',
    body: (
      <>
        <p>
          Mientras la cuenta esté activa, ALIBI conserva los archivos cargados y la información derivada de ellos (los
          valores de muestra de cada columna inferida, las tablas de resultado del historial de conversaciones y las de
          los gráficos guardados). En el plan Free, el historial alcanza los últimos siete días.
        </p>
        <p>
          Cuando el usuario elimina su cuenta, dispone de <strong>treinta días</strong> para exportar sus datos. Vencido
          ese plazo, los archivos y toda la información derivada se eliminan en forma definitiva. Se conservan solo los
          datos de facturación que las normas fiscales obligan a guardar.
        </p>
      </>
    ),
  },
  {
    id: 'responsabilidad',
    title: 'Responsabilidad',
    body: (
      <>
        <p>
          El servicio se presta <strong>tal cual</strong> y según su disponibilidad, sin garantía de exactitud de los
          resultados ni de su adecuación a un fin determinado. Puede tener interrupciones por mantenimiento o por causas
          ajenas a ALIBI; tras un período sin uso, la primera consulta puede demorar mientras el sistema se reactiva.
        </p>
        <p>
          La responsabilidad de ALIBI se limita a los <strong>daños directos</strong> y hasta el monto que el usuario
          haya abonado en los <strong>doce meses</strong> anteriores al hecho que origina el reclamo. Se excluyen el
          lucro cesante y los daños indirectos. Esta limitación no alcanza a los daños causados por dolo o culpa grave ni
          restringe los derechos que la ley reconoce a los consumidores.
        </p>
      </>
    ),
  },
  {
    id: 'indemnidad',
    title: 'Indemnidad',
    body: (
      <p>
        El usuario mantendrá indemne a ALIBI frente a cualquier reclamo de terceros, incluidos los titulares de los
        datos personales que cargó, que derive de los datos cargados o del uso que el usuario hizo de los resultados.
      </p>
    ),
  },
  {
    id: 'modificaciones',
    title: 'Modificaciones',
    body: (
      <p>
        ALIBI puede modificar estos Términos. Los cambios se avisan por correo electrónico y dentro de la aplicación con
        treinta días de anticipación. Si el usuario no está de acuerdo, puede dar de baja la suscripción antes de que
        entren en vigor; seguir usando el servicio después implica aceptarlos.
      </p>
    ),
  },
  {
    id: 'jurisdiccion',
    title: 'Ley aplicable y jurisdicción',
    body: (
      <p>
        Estos Términos se rigen por las leyes de la República Argentina. Toda controversia se somete a los tribunales
        ordinarios de la Ciudad Autónoma de Buenos Aires, salvo que el usuario sea consumidor, en cuyo caso será
        competente el tribunal de su domicilio.
      </p>
    ),
  },
  {
    id: 'contacto',
    title: 'Contacto',
    body: <p>Consultas sobre estos Términos, la facturación o la cuenta: {mail}.</p>,
  },
]

export const TERMS: LegalDocContent = {
  title: 'Términos y condiciones',
  lead: (
    <>
      Las reglas del uso de ALIBI: qué ofrece, qué datos podés cargar, cómo se cobran los planes y qué alcance tienen
      los resultados. Leélos junto con la <LegalLink doc="privacy">Política de privacidad</LegalLink>.
    </>
  ),
  sections: SECTIONS,
}
