import { SUPPORT_EMAIL } from '../../lib/legal'
import { LegalCallout, type LegalDocContent, type LegalSection } from './legalParts'
import { LegalLink } from './LegalLink'

/*
 * Privacy Policy (Ley 25.326). Content follows the thesis (Capítulo 5,
 * "Protección de los datos personales"). Facts about the system stated here are
 * the code's: what reaches the inference platform (≤ 5 sample values per
 * column, an excerpt for an ambiguous header, ≤ 30 result rows —
 * backend/app/core/config.py `narrative_max_prompt_rows`), AWS us-east-1,
 * storage encrypted at rest (terraform: RDS `storage_encrypted`, S3 SSE).
 */

const mail = <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>

const SECTIONS: LegalSection[] = [
  {
    id: 'roles',
    title: 'Quién trata tus datos y en qué carácter',
    body: (
      <>
        <p>ALIBI trata dos clases de datos, con obligaciones distintas:</p>
        <ul>
          <li>
            <strong>Los datos de tu cuenta.</strong> Respecto de ellos, ALIBI es <strong>responsable de la base de
            datos</strong>, en los términos de la Ley 25.326 de Protección de los Datos Personales.
          </li>
          <li>
            <strong>El contenido de los archivos que cargás.</strong> Pueden contener datos personales de terceros
            (clientes, empleados). ALIBI los trata por tu cuenta, como <strong>prestador de servicios
            informatizados</strong> (artículo 25 de la ley): solo para prestarte el servicio, sin cederlos ni usarlos
            para otro fin. Vos seguís siendo responsable frente a sus titulares, como establecen los{' '}
            <LegalLink doc="terms">Términos</LegalLink>.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: 'datos-cuenta',
    title: 'Datos de tu cuenta que recolectamos',
    body: (
      <>
        <p>Solo los necesarios para operar el servicio:</p>
        <ul>
          <li>
            <strong>Identificatorios y de contacto:</strong> nombre, correo electrónico y nombre de la organización.
          </li>
          <li>
            <strong>De autenticación:</strong> la contraseña, que se guarda solo como resumen criptográfico (nunca en
            texto legible), los códigos de verificación y las sesiones activas.
          </li>
          <li>
            <strong>De facturación:</strong> plan contratado, facturas emitidas y, del medio de pago, solo la marca, los
            últimos cuatro dígitos y el vencimiento. El número completo de la tarjeta queda en poder del procesador de
            pagos.
          </li>
          <li>
            <strong>De uso:</strong> preferencias, notificaciones y el consumo registrado por cada consulta respondida
            (fecha, filas devueltas y costo), que se usa para calcular tu cupo y tus consultas adicionales.
          </li>
          <li>
            <strong>De aceptación:</strong> la versión de estos documentos que aceptaste y la fecha en que lo hiciste.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: 'finalidades',
    title: 'Para qué los usamos',
    body: (
      <ul>
        <li>Prestarte el servicio y mantener tu cuenta segura.</li>
        <li>Facturar, cobrar y cumplir las obligaciones fiscales.</li>
        <li>Avisarte de tu consumo (por ejemplo, al llegar al 80 % de tu cupo) y de cambios en el servicio.</li>
        <li>
          Enviarte novedades del producto, solo si lo aceptás; podés desactivarlas en Cuenta → Preferencias.
        </li>
      </ul>
    ),
  },
  {
    id: 'archivos',
    title: 'Qué hacemos con tus archivos',
    body: (
      <>
        <p>
          Tus datos se almacenan aislados de los de otros usuarios y solo se consultan en modo de lectura: ALIBI nunca
          los modifica. <strong>Los modelos no se entrenan con datos de usuarios.</strong>
        </p>
        <p>
          Para responder, ALIBI usa una plataforma de inferencia de inteligencia artificial, a la que llega solo lo
          indispensable, nunca la tabla completa:
        </p>
        <ul>
          <li>el perfil de cada columna, con a lo sumo cinco valores de muestra;</li>
          <li>un extracto del documento, cuando un encabezado es ambiguo;</li>
          <li>para redactar la explicación de cada respuesta, hasta treinta filas del resultado de la consulta.</li>
        </ul>
        <p>
          Además de los archivos, ALIBI conserva información derivada de ellos: los valores de muestra de cada columna,
          las tablas de resultado de tu historial de conversaciones y las de los gráficos que guardás. Los tableros
          guardan la consulta, no sus valores. Toda esa información recibe el mismo trato que los archivos y se elimina
          con ellos.
        </p>
      </>
    ),
  },
  {
    id: 'sensibles',
    title: 'Datos sensibles',
    body: (
      <p>
        ALIBI no está diseñado para tratar datos sensibles (salud, origen étnico, opiniones políticas, convicciones
        religiosas, afiliación sindical, vida sexual) y los <LegalLink doc="terms">Términos</LegalLink> prohíben cargarlos.
        Si se cargan igualmente, es en infracción de los Términos y bajo exclusiva responsabilidad de quien lo hace.
      </p>
    ),
  },
  {
    id: 'proveedores',
    title: 'Proveedores y transferencia internacional',
    body: (
      <>
        <p>
          ALIBI no vende ni cede tus datos. Para operar recurre a proveedores que los tratan por su cuenta y bajo su
          instrucción:
        </p>
        <ul>
          <li>
            <strong>Amazon Web Services</strong>, que provee la infraestructura, el almacenamiento y la plataforma de
            inferencia (Amazon Bedrock y Amazon SageMaker), en servidores ubicados en los Estados Unidos;
          </li>
          <li>el <strong>procesador de pagos</strong>, que recibe los datos de la tarjeta para cobrar la suscripción.</li>
        </ul>
        <p>
          Como los servidores están fuera de la Argentina, usar ALIBI implica una <strong>transferencia internacional
          de datos</strong>. Al registrarte prestás tu <strong>consentimiento expreso</strong> para esa transferencia, y
          ALIBI la respalda además con las cláusulas contractuales modelo aprobadas por la Agencia de Acceso a la
          Información Pública (Disposición 60-E/2016).
        </p>
      </>
    ),
  },
  {
    id: 'seguridad',
    title: 'Seguridad',
    body: (
      <p>
        Los datos viajan cifrados (HTTPS) y se almacenan cifrados. Cada usuario accede solo a sus propios datos, las
        consultas sobre tus archivos son de solo lectura, la base de datos no es accesible desde internet y las
        contraseñas se guardan como resumen criptográfico. Ningún sistema es invulnerable: si ocurriera un incidente que
        afecte tus datos, te lo vamos a informar.
      </p>
    ),
  },
  {
    id: 'conservacion',
    title: 'Cuánto tiempo los conservamos',
    body: (
      <p>
        Mientras tu cuenta esté activa. Si la eliminás, tenés treinta días para exportar tus datos; vencido ese plazo,
        los archivos, la información derivada y los datos de tu cuenta se eliminan en forma definitiva, salvo los de
        facturación, que se conservan durante el plazo que exigen las normas fiscales y contables.
      </p>
    ),
  },
  {
    id: 'derechos',
    title: 'Tus derechos',
    body: (
      <>
        <p>
          Podés ejercer los derechos de <strong>acceso, rectificación, actualización y supresión</strong> de tus datos
          escribiendo a {mail}, y corregir tu nombre y correo en Cuenta → Perfil. Respondemos los pedidos de acceso
          dentro de los diez días corridos y los de rectificación o supresión dentro de los cinco días hábiles, como
          establece la ley. También podés retirar el consentimiento para comunicaciones del producto en cualquier
          momento.
        </p>
        <LegalCallout>
          El titular de los datos personales tiene la facultad de ejercer el derecho de acceso a los mismos en forma
          gratuita a intervalos no inferiores a seis meses, salvo que se acredite un interés legítimo al efecto conforme
          lo establecido en el artículo 14, inciso 3 de la Ley N° 25.326. La Agencia de Acceso a la Información Pública,
          en su carácter de Órgano de Control de la Ley N° 25.326, tiene la atribución de atender las denuncias y
          reclamos que interpongan quienes resulten afectados en sus derechos por incumplimiento de las normas vigentes
          en materia de protección de datos personales.
        </LegalCallout>
        <p>
          Si los datos que querés consultar o suprimir son de un archivo que cargó una empresa (por ejemplo, la tuya
          como cliente o empleado), el pedido corresponde a esa empresa, que es la responsable de esos datos.
        </p>
      </>
    ),
  },
  {
    id: 'almacenamiento-local',
    title: 'Almacenamiento en tu navegador',
    body: (
      <p>
        ALIBI guarda en tu navegador solo lo necesario para mantener tu sesión iniciada y recordar algunas preferencias
        de la interfaz. No usa cookies de publicidad ni herramientas de seguimiento de terceros.
      </p>
    ),
  },
  {
    id: 'cambios',
    title: 'Cambios en esta política',
    body: (
      <p>
        Si esta política cambia en algo sustancial, te avisamos por correo electrónico y dentro de la aplicación con
        treinta días de anticipación.
      </p>
    ),
  },
  {
    id: 'contacto',
    title: 'Contacto',
    body: <p>Para cualquier consulta sobre tus datos personales: {mail}.</p>,
  },
]

export const PRIVACY: LegalDocContent = {
  title: 'Política de privacidad',
  lead: (
    <>
      Qué datos tratamos, para qué, con quién y cómo podés ejercer tus derechos según la Ley 25.326. Forma parte de
      los <LegalLink doc="terms">Términos y condiciones</LegalLink>.
    </>
  ),
  sections: SECTIONS,
}
