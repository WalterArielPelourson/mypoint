from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify, g, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_moment import Moment # Importa Flask-Moment
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import requests
import time
from datetime import datetime, timedelta
import io
import csv
import json
import os
from database import inicializar_db, db_query as db_query_func, db_execute as db_execute_func
from itertools import zip_longest 







# --- CONFIGURACIÓN DE RUTAS ---
# Esto detecta la carpeta donde está este archivo app.py
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Unimos la carpeta con el nombre del archivo para tener la ruta completa
DB_NAME = os.path.join(BASE_DIR, "negocio_erp.db")




# --- INICIALIZACIÓN DE LA APP Y GESTIÓN DE DB ---
app = Flask(__name__)
#app.secret_key = 'clave_final_super_secreta_cambiar_en_produccion'
app.secret_key = os.environ.get('SECRET_KEY', 'desarrollo_local_clave_9911')
DB_NAME = "negocio_erp.db"

moment = Moment(app)

# --- NUEVA DEFINICIÓN: Sub-rubros para movimientos manuales de caja ---
SUB_RUBROS_CAJA = [
    'Alquileres',
    'Aportes Socios',
    'Retiros Socios',
    'Sueldos',
    'Impuestos',
    'Comestibles',
    'Limpieza',
    'Muebles y Útiles',
    'Bienes de Uso',
    'Otros'
]


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_NAME)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def db_query(query, params=()):
    return db_query_func(get_db(), query, params)

def db_execute(query, params=(), return_id=False):
    return db_execute_func(get_db(), query, params, return_id)

# --- CONFIGURACIÓN DE LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, inicie sesión para acceder a esta página."
login_manager.login_message_category = "info"

# --- MODELO DE USUARIO ---
class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = id
        self.username = username
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    # Buscamos el usuario por ID y traemos su ROL y su estado de ACTIVACIÓN
    user_data = db_query("SELECT id, username, role, active FROM users WHERE id = ?", (user_id,))
    
    if user_data:
        u = user_data[0]
        # USA CORCHETES, no .get()
        if u['active'] == 1:
            return User(id=u['id'], username=u['username'], role=u['role'])
            
    return None

#@login_manager.user_loader
#def load_user(user_id):
#    user_data = db_query("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
#    if user_data:
#        user = user_data[0]
#        return User(id=user['id'], username=user['username'], role=user['role'])
#    return None

# --- DECORADOR PARA ROLES DE ADMINISTRADOR ---
#def admin_required(f):
#    @wraps(f)
#    def decorated_function(*args, **kwargs):
#        if not current_user.is_authenticated or current_user.role != 'admin':
#            flash("Se requiere acceso de administrador para esta página.", "danger")
#            return redirect(url_for('index'))
#        return f(*args, **kwargs)
#    return decorated_function

# Modificar para que 'usuario' también pueda entrar a funciones administrativas de operación
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Ahora permitimos 'usuario' además de admin y superadmin
        if not current_user.is_authenticated or current_user.role not in ['admin', 'superadmin', 'usuario']:
            flash("Se requieren permisos de operador o administrador.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def superadmin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'superadmin':
            flash("Solo el SuperAdmin puede acceder a esta sección.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


# Modificar para que 'usuario' también pueda usar funciones de técnico
def tecnico_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Ahora permitimos 'usuario' además de tecnico, admin y superadmin
        if not current_user.is_authenticated or current_user.role not in ['tecnico', 'admin', 'superadmin', 'usuario']:
            flash("Acceso restringido.", "warning")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function



# Decorador específico para restringir Cuentas Virtuales
def restriction_usuario(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role == 'usuario':
            flash("Tu nivel de usuario no tiene acceso a Cuentas Virtuales.", "warning")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

#Crea Cuentas virtuales
@app.route('/admin/configuracion/cuentas')
@login_required
@superadmin_required
def gestionar_cuentas_virtuales():
    cuentas = db_query("SELECT * FROM cuentas_entidades")
    return render_template('admin/cuentas_virtuales.html', cuentas=cuentas)

@app.route('/admin/configuracion/cuentas/nueva', methods=['POST'])
@login_required
@superadmin_required
def crear_cuenta_virtual():
    # Normalizamos el nombre: mayúsculas y sin espacios
    nombre = request.form.get('nombre', '').strip().upper().replace(" ", "_")
    titular = request.form.get('titular', '').strip()

    if not nombre or not titular:
        flash("Nombre y Titular son obligatorios.", "danger")
    else:
        try:
            db_execute("INSERT INTO cuentas_entidades (nombre, titular) VALUES (?, ?)", (nombre, titular))
            flash(f"Cuenta '{nombre}' registrada exitosamente.", "success")
        except Exception as e:
            flash(f"Error al crear cuenta: {e}", "danger")
    
    return redirect(url_for('gestionar_cuentas_virtuales'))


@app.route('/admin/configuracion/cuentas/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@superadmin_required
def editar_cuenta_virtual(id):
    cuenta_data = db_query("SELECT * FROM cuentas_entidades WHERE id = ?", (id,))
    if not cuenta_data:
        flash("Cuenta no encontrada.", "danger")
        return redirect(url_for('gestionar_cuentas_virtuales'))
    
    cuenta = cuenta_data[0]

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip().upper().replace(" ", "_")
        titular = request.form.get('titular', '').strip()
        activo = 1 if 'activo' in request.form else 0

        if not nombre or not titular:
            flash("Todos los campos son obligatorios.", "danger")
        else:
            try:
                db_execute("""
                    UPDATE cuentas_entidades 
                    SET nombre = ?, titular = ?, activo = ? 
                    WHERE id = ?
                """, (nombre, titular, activo, id))
                flash(f"Cuenta '{nombre}' actualizada.", "success")
                return redirect(url_for('gestionar_cuentas_virtuales'))
            except Exception as e:
                flash(f"Error: {e}", "danger")

    return render_template('admin/editar_cuenta_virtual.html', cuenta=cuenta)


@app.route('/admin/configuracion/cuentas/toggle_status/<int:id>', methods=['POST'])
@login_required
@superadmin_required
def toggle_cuenta_virtual(id):
    # Buscamos el estado actual de la cuenta
    cuenta = db_query("SELECT activo, nombre FROM cuentas_entidades WHERE id = ?", (id,))
    
    if not cuenta:
        flash("Cuenta no encontrada.", "danger")
        return redirect(url_for('gestionar_cuentas_virtuales'))
    
    # Invertimos el estado (si es 1 pasa a 0, si es 0 a 1)
    nuevo_estado = 0 if cuenta[0]['activo'] == 1 else 1
    db_execute("UPDATE cuentas_entidades SET activo = ? WHERE id = ?", (nuevo_estado, id))
    
    accion = "desactivada (inactiva)" if nuevo_estado == 0 else "activada"
    flash(f"La cuenta '{cuenta[0]['nombre']}' ha sido {accion}.", "info")
    
    return redirect(url_for('gestionar_cuentas_virtuales'))

#---- Nuevo Logueo-----
#@app.route('/admin/usuarios')
#@login_required
#@superadmin_required
#def gestionar_usuarios():
#    usuarios = db_query("SELECT id, username, role FROM users WHERE username != 'superadmin'")
#    return render_template('admin/usuarios.html', usuarios=usuarios)

@app.route('/admin/usuarios')
@login_required
@superadmin_required
def gestionar_usuarios():
    # Agregamos 'active' a la consulta
    usuarios = db_query("SELECT id, username, role, active FROM users WHERE username != 'superadmin'")
    return render_template('admin/usuarios.html', usuarios=usuarios)



@app.route('/admin/usuarios/nuevo', methods=['POST'])
@login_required
@superadmin_required
def crear_usuario():
    # Capturamos y normalizamos el username a minúsculas y sin espacios laterales
    username = request.form.get('username', '').lower().strip()
    password = request.form.get('password', '')
    role = request.form.get('role') # Recibirá 'admin', 'usuario' o 'tecnico'

    # Validación básica de campos vacíos
    if not username or not password:
        flash("Nombre de usuario y contraseña son obligatorios.", "danger")
        return redirect(url_for('gestionar_usuarios'))

    # Verificamos si el usuario ya existe
    if db_query("SELECT id FROM users WHERE username = ?", (username,)):
        flash(f"El nombre de usuario '{username}' ya existe.", "danger")
    else:
        try:
            # Generamos el Hash de seguridad usando el método compatible con check_password_hash
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            
            # Ejecutamos la inserción en la base de datos con el rol seleccionado (incluyendo 'tecnico')
            db_execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                       (username, hashed_pw, role))
            
            flash(f"Usuario {username} creado exitosamente como {role}.", "success")
        except Exception as e:
            app.logger.error(f"Error al crear usuario: {e}")
            flash("Error inesperado al crear el usuario. Intente nuevamente.", "danger")
    
    return redirect(url_for('gestionar_usuarios'))



@app.route('/admin/usuarios/toggle_status/<int:user_id>', methods=['POST'])
@login_required
@superadmin_required
def toggle_usuario_status(user_id):
    # Impedir que el superadmin se inactive a sí mismo
    if user_id == current_user.id:
        flash("No puedes inactivar tu propia cuenta de administrador.", "danger")
        return redirect(url_for('gestionar_usuarios'))

    user_data = db_query("SELECT active, username FROM users WHERE id = ?", (user_id,))
    if not user_data:
        flash("Usuario no encontrado.", "danger")
        return redirect(url_for('gestionar_usuarios'))

    # Cambiamos el estado
    nuevo_estado = 0 if user_data[0]['active'] == 1 else 1
    db_execute("UPDATE users SET active = ? WHERE id = ?", (nuevo_estado, user_id))
    
    estado_texto = "activado" if nuevo_estado == 1 else "inactivado"
    flash(f"Usuario '{user_data[0]['username']}' {estado_texto} correctamente.", "info")
    return redirect(url_for('gestionar_usuarios'))


@app.route('/admin/usuarios/editar/<int:user_id>', methods=['GET', 'POST'])
@login_required
@superadmin_required
def editar_usuario(user_id):
    user_data = db_query("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user_data:
        flash("Usuario no encontrado.", "danger")
        return redirect(url_for('gestionar_usuarios'))
    
    usuario = dict(user_data[0])

    if request.method == 'POST':
        username = request.form.get('username', '').lower().strip()
        role = request.form.get('role')
        password = request.form.get('password', '')

        if not username or not role:
            flash("Nombre y rol son requeridos.", "danger")
        else:
            try:
                db_execute("UPDATE users SET username = ?, role = ? WHERE id = ?", (username, role, user_id))
                if password: # Si se escribió una nueva contraseña, se hashea y se guarda
                    hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
                    db_execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pw, user_id))
                flash(f"Usuario {username} actualizado.", "success")
                return redirect(url_for('gestionar_usuarios'))
            except Exception as e:
                flash(f"Error: {e}", "danger")

    return render_template('admin/editar_usuario.html', usuario=usuario)


# --- FUNCIÓN AUXILIAR PARA REGISTRAR MOVIMIENTOS ---
def registrar_movimiento(user_id, tipo_movimiento, tipo_item, item_id=None, detalles=None):
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detalles_json = json.dumps(detalles, default=str) if detalles else None # default=str para objetos no serializables
    db_execute(
        "INSERT INTO movimientos (user_id, tipo_movimiento, tipo_item, item_id, fecha, detalles) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, tipo_movimiento, tipo_item, item_id, fecha_actual, detalles_json)
    )

# --- FUNCIÓN AUXILIAR PARA REGISTRAR MOVIMIENTOS DE CAJA (Ahora con ARS y USD y sub_categoria) ---
def registrar_movimiento_caja(user_id, tipo, monto_ars=0, monto_usd=0, descripcion=None, referencia_id=None, sub_categoria=None, metodo_pago='EFECTIVO'):
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Aseguramos que si no viene metodo_pago (o es None/vacío), se guarde como EFECTIVO
    if not metodo_pago:
        metodo_pago = 'EFECTIVO'

    db_execute(
        "INSERT INTO caja_movimientos (user_id, fecha, tipo, monto_ars, monto_usd, descripcion, referencia_id, sub_categoria, metodo_pago) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, fecha_actual, tipo, monto_ars, monto_usd, descripcion, referencia_id, sub_categoria, metodo_pago)
    )
    
# --- RUTAS DE AUTENTICACIÓN ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').lower().strip()
        password = request.form.get('password')
        
        user_data = db_query("SELECT * FROM users WHERE username = ?", (username,))
        
        if user_data and check_password_hash(user_data[0]['password'], password):
            
            # CAMBIO AQUÍ: Usa corchetes para acceder a la columna
            if user_data[0]['active'] == 0:
                flash("Tu cuenta está desactivada. Por favor, contacta al administrador.", "danger")
                return redirect(url_for('login'))

            user_obj = User(
                id=user_data[0]['id'], 
                username=user_data[0]['username'], 
                role=user_data[0]['role']
            )
            login_user(user_obj)
            
            flash(f"Bienvenido {user_obj.username}. Nivel: {user_obj.role}", "success")
            return redirect(url_for('index'))
        else:
            flash("Usuario o contraseña incorrectos.", "danger")
            return redirect(url_for('login'))
            
    return render_template('login.html')



@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Validación de campos obligatorios
        if not username or not password:
            flash("Nombre de usuario y contraseña son obligatorios.", "danger")
            return redirect(url_for('registro'))
            
        user_exists = db_query("SELECT * FROM users WHERE username = ?", (username,))
        if user_exists:
            flash("El nombre de usuario ya existe.", "danger")
            return redirect(url_for('registro'))
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        is_first_user = not db_query("SELECT id FROM users LIMIT 1")
        role = 'admin' if is_first_user else 'vendedor'
        db_execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (username, hashed_password, role))
        flash(f"Usuario {username} creado como {role}. Ahora puede iniciar sesión.", "success")
        return redirect(url_for('login'))
    return render_template('registro.html')

@app.route('/logout')
@login_required
def logout():
    # Antes de cerrar sesión, verificar si hay una caja abierta (only for admins)
    #if current_user.role == 'admin':
    #    caja_abierta = db_query("SELECT id FROM arqueo_caja WHERE user_id = ? AND estado = 'ABIERTO'", (current_user.id,))
    #    if caja_abierta:
    #        flash("Por favor, cierre la caja antes de cerrar sesión.", "warning")
    #        return redirect(url_for('arqueo_caja')) # Redirigir a la página de arqueo para que la cierre
            
    logout_user()
    flash("Ha cerrado sesión exitosamente.", "success")
    return redirect(url_for('login'))

# --- CONFIGURACIÓN DE DÓLAR ACTUALIZADA (BCRA Y DOLAR HOY) ---
# Estructura para almacenar ambas cotizaciones y el tiempo de la última actualización
valor_dolar_cache = {
    'bcra': {'compra': None, 'venta': None},
    'dolar_hoy': {'compra': None, 'venta': None},
    'timestamp': 0
}

# URLs de la API (DolarAPI consolida BCRA/Oficial y Blue/Dolar Hoy)
API_URL_OFICIAL = "https://dolarapi.com/v1/dolares/oficial"
API_URL_BLUE = "https://dolarapi.com/v1/dolares/blue"





def obtener_cotizacion_dolar():
    # Actualizar cada 1800 segundos (30 minutos) según requerimiento
    if time.time() - valor_dolar_cache['timestamp'] > 1800: 
        try:
            # 1. Petición para Dólar Oficial (Banco Central)
            r_oficial = requests.get(API_URL_OFICIAL, timeout=10)
            r_oficial.raise_for_status()
            data_oficial = r_oficial.json()
            
            # 2. Petición para Dólar Blue (Dolar Hoy)
            r_blue = requests.get(API_URL_BLUE, timeout=10)
            r_blue.raise_for_status()
            data_blue = r_blue.json()

            # Asegurar que los valores de Oficial sean float
            compra_val = float(data_oficial.get('compra')) if data_oficial.get('compra') is not None else None
            venta_val = float(data_oficial.get('venta')) if data_oficial.get('venta') is not None else None
            
            # Asegurar que los valores de Blue (Dolar Hoy) sean float
            compra_blue_val = float(data_blue.get('compra')) if data_blue.get('compra') is not None else None
            venta_blue_val = float(data_blue.get('venta')) if data_blue.get('venta') is not None else None
            
            # Actualizar el caché con ambas procedencias
            valor_dolar_cache.update({
                'compra': compra_val, 
                'venta': venta_val, 
                'compra_blue': compra_blue_val,
                'venta_blue': venta_blue_val,
                'timestamp': time.time()
            })
            
        except requests.RequestException as e:
            app.logger.error(f"Error al obtener cotización del dólar: {e}")
            # Mantener el valor anterior si no se pudo actualizar
            if valor_dolar_cache['compra'] is None or valor_dolar_cache['venta'] is None:
                flash("Error al obtener la cotización del dólar. Algunas funciones podrían estar limitadas.", "danger")
            # Retornar los valores en caché (que ya deberían ser float o None)
            return valor_dolar_cache
            
        except ValueError as e: # Catch if conversion to float fails
            app.logger.error(f"Error al convertir cotización del dólar a float: {e}")
            if valor_dolar_cache['compra'] is None or valor_dolar_cache['venta'] is None:
                flash("Error al obtener y procesar la cotización del dólar. Algunas funciones podrían estar limitadas.", "danger")
            return valor_dolar_cache
            
    # Retornar los valores en caché (que ya deberían ser float o None)
    return valor_dolar_cache


# --- FUNCIÓN AUXILIAR PARA FILTROS DE FECHA ---
def get_date_filters():
    today = datetime.now()
    
    # Obtener start_date de los argumentos de la request, o usar la fecha de hace 30 días si no existe
    # Si el valor de request.args.get('start_date') es una cadena vacía, default a la fecha calculada
    start_date_raw = request.args.get('start_date', '').strip()
    if not start_date_raw:
        start_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    else:
        start_date = start_date_raw

    # Obtener end_date de los argumentos de la request, o usar la fecha actual si no existe
    # Si el valor de request.args.get('end_date') es una cadena vacía, default a la fecha actual
    end_date_display_raw = request.args.get('end_date', '').strip()
    if not end_date_display_raw:
        end_date_display = today.strftime('%Y-%m-%d')
    else:
        end_date_display = end_date_display_raw

    # Para la consulta de DB, el `end_date` debe incluir todo el día final
    # Asegurarse de que end_date_display no esté vacío antes de strptime
    try:
        end_date_query = (datetime.strptime(end_date_display, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    except ValueError:
        # En caso de que end_date_display sea inválido (aunque ya lo filtramos arriba),
        # usar la fecha actual para la consulta
        end_date_query = (today + timedelta(days=1)).strftime('%Y-%m-%d')

    return start_date, end_date_display, end_date_query

# ... (el resto de tu app.py) ...

# --- FILTRO DATETIMEFORMAT PARA JINJA2 (SOLUCIÓN AL ERROR) ---
def format_datetime(value, format="%d/%m/%Y %H:%M:%S"):
    """
    Formatea un objeto datetime a una cadena de texto.
    Si el valor es None, devuelve una cadena vacía.
    Se puede personalizar el formato.
    """
    if value is None:
        return ""
    # Si el valor es una cadena, intenta convertirlo a datetime
    if isinstance(value, str):
        try:
            # Asume un formato ISO para la cadena, ajusta si tus fechas son diferentes
            # Intenta con varios formatos si el formato es inconsistente
            for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    return datetime.strptime(value, fmt).strftime(format)
                except ValueError:
                    continue
            return value # Si no se puede parsear, devuelve la cadena original
        except ValueError:
            # Si no se puede parsear después de varios intentos, devuelve la cadena original
            return value 
    
    if isinstance(value, datetime):
        return value.strftime(format)
    # En caso de que no sea datetime ni None (e.g., int, float),
    # puedes optar por devolver su representación de cadena o un error.
    return str(value)

# Registra la función como un filtro de Jinja2 en el entorno de la aplicación
app.jinja_env.filters['datetimeformat'] = format_datetime


# --- CONTEXT PROCESSOR PARA PASAR EL DÓLAR AL LAYOUT ---
# --- CONTEXT PROCESSOR ACTUALIZADO ---
@app.context_processor
def inject_dolar_values():
    dolar_info = obtener_cotizacion_dolar()
    
    # --- VALORES BANCO CENTRAL (OFICIAL) ---
    compra = float(dolar_info['compra']) if dolar_info['compra'] is not None else None
    venta = float(dolar_info['venta']) if dolar_info['venta'] is not None else None
    
    # --- VALORES DOLAR HOY (BLUE) ---
    compra_blue = float(dolar_info['compra_blue']) if dolar_info['compra_blue'] is not None else None
    venta_blue = float(dolar_info['venta_blue']) if dolar_info['venta_blue'] is not None else None
    
    # --- NUEVO: OBTENER CUENTAS VIRTUALES CONFIGURADAS ---
    # Esto permite que aparezcan automáticamente en todos los selectores de cobro/pago
    try:
        cuentas_sistema = db_query("SELECT id, nombre, titular FROM cuentas_entidades WHERE activo = 1 ORDER BY nombre ASC")
    except:
        cuentas_sistema = []

    return dict(
        # Variables identificadas por procedencia para la barra superior
        valor_bcra_compra=compra,
        valor_bcra_venta=venta,
        valor_blue_compra=compra_blue,
        valor_blue_venta=venta_blue,
        
        # Mantenemos las variables originales
        valor_dolar_compra=compra_blue, 
        valor_dolar_venta=venta_blue,
        
        # NUEVA VARIABLE GLOBAL PARA TEMPLATES
        cuentas_entidades=cuentas_sistema
    )

# --- RUTAS PRINCIPALES Y DASHBOARD ---
@app.route('/')
@login_required
def index():
    # Definimos los roles que operan caja
    if current_user.role in ['admin', 'superadmin', 'usuario']:
        # CAMBIO CLAVE: Eliminamos "user_id = ?" para que el sistema detecte 
        # si hay una caja abierta globalmente en el local, sin importar quién la abrió.
        caja_abierta = db_query("SELECT id FROM arqueo_caja WHERE estado = 'ABIERTO'")
        
        if not caja_abierta:
            flash("Debes realizar la apertura de caja (ARS y USD) para empezar el día.", "info")
            return redirect(url_for('arqueo_caja'))

    # --- TODO EL RESTO DEL CÓDIGO SE MANTIENE SIN OMISIONES ---
    start_date, end_date_display, end_date_query = get_date_filters()
    
    params = (start_date, end_date_query)
    resumen_ventas = db_query("SELECT COUNT(id) as total_ventas, SUM(precio_final_ars) as facturacion_total FROM ventas WHERE status = 'COMPLETADA' AND fecha_venta BETWEEN ? AND ?", params)[0]
    resumen_reparaciones = db_query("SELECT COUNT(id) as total_reparaciones, SUM(precio_final_ars) as facturacion_total FROM servicios_reparacion WHERE status = 'COMPLETADO' AND fecha_servicio BETWEEN ? AND ?", params)[0]
    
    facturacion_unificada = (resumen_ventas['facturacion_total'] or 0.0) + (resumen_reparaciones['facturacion_total'] or 0.0)
    
    # --- LÓGICA: CONTAR CUMPLEAÑOS DE HOY ---
    hoy_dt = datetime.now()
    hoy_mm_dd = hoy_dt.strftime('%m-%d')
    res_cumple = db_query("SELECT COUNT(id) as total FROM personas WHERE strftime('%m-%d', fecha_nacimiento) = ?", (hoy_mm_dd,))
    cumple_hoy = res_cumple[0]['total'] if res_cumple else 0
    
    # --- LÓGICA: CONTAR CUOTAS (HOY Y VENCIDAS) ---
    hoy_fijo = hoy_dt.strftime('%Y-%m-%d')
    
    # 1. Cuotas que vencen hoy estrictamente
    res_vencimientos = db_query("""
        SELECT COUNT(id) as total 
        FROM ventas_cuotas 
        WHERE fecha_vencimiento = ? AND estado = 'PENDIENTE'
    """, (hoy_fijo,))
    vencimientos_hoy = res_vencimientos[0]['total'] if res_vencimientos else 0

    # 2. Cuotas ya vencidas (fecha anterior a hoy y siguen pendientes)
    res_vencidas_atrasadas = db_query("""
        SELECT COUNT(id) as total 
        FROM ventas_cuotas 
        WHERE fecha_vencimiento < ? AND estado = 'PENDIENTE'
    """, (hoy_fijo,))
    cuotas_vencidas = res_vencidas_atrasadas[0]['total'] if res_vencidas_atrasadas else 0
    
    return render_template('index.html',
                           resumen_ventas=resumen_ventas,
                           resumen_reparaciones=resumen_reparaciones,
                           facturacion_unificada=facturacion_unificada,
                           start_date=start_date, 
                           end_date=end_date_display,
                           cumple_hoy=cumple_hoy,
                           vencimientos_hoy=vencimientos_hoy,
                           cuotas_vencidas=cuotas_vencidas)
        
# =================================================================
# === MÓDULO DE GESTIÓN DE PERSONAS (CLIENTES/PROVEEDORES) ========
# =================================================================
@app.route('/personas')
@login_required
def listar_personas():
    filtro_nombre = request.args.get('nombre', '').strip()
    filtro_cuit = request.args.get('cuit_cuil', '').strip()
    filtro_es_cliente = request.args.get('es_cliente') == '1'
    filtro_es_proveedor = request.args.get('es_proveedor') == '1'

    query = "SELECT * FROM personas WHERE 1=1"
    params = []

    if filtro_nombre:
        query += " AND (nombre LIKE ? OR apellido LIKE ? OR razon_social LIKE ?)"
        params.extend([f"%{filtro_nombre}%", f"%{filtro_nombre}%", f"%{filtro_nombre}%"])
    if filtro_cuit:
        query += " AND cuit_cuil LIKE ?"
        params.append(f"%{filtro_cuit}%")
    if filtro_es_cliente:
        query += " AND es_cliente = 1"
    if filtro_es_proveedor:
        query += " AND es_proveedor = 1"
    
    query += " ORDER BY razon_social, apellido, nombre"
    personas = db_query(query, tuple(params))
    return render_template('personas/listar.html', personas=personas, 
                           filtros_activos={'nombre': filtro_nombre, 'cuit_cuil': filtro_cuit, 'es_cliente': filtro_es_cliente, 'es_proveedor': filtro_es_proveedor})

@app.route('/personas/nueva', methods=['GET', 'POST'])
@login_required
@admin_required
def agregar_persona():
    if request.method == 'POST':
        # Recolectar todos los datos del formulario al principio del POST
        nombre = request.form.get('nombre', '').strip()
        apellido = request.form.get('apellido', '').strip()
        razon_social = request.form.get('razon_social', '').strip()
        cuit_cuil = request.form.get('cuit_cuil', '').strip()
        telefono = request.form.get('telefono', '').strip()
        email = request.form.get('email', '').strip()
        fecha_nacimiento = request.form.get('fecha_nacimiento', '') # NUEVO CAMPO
        es_cliente = 'es_cliente' in request.form 
        es_proveedor = 'es_proveedor' in request.form

        # Almacenar los datos para re-renderizar la plantilla en caso de error
        form_data = {
            'nombre': nombre, 'apellido': apellido, 'razon_social': razon_social,
            'cuit_cuil': cuit_cuil, 'telefono': telefono, 'email': email,
            'fecha_nacimiento': fecha_nacimiento, # NUEVO CAMPO
            'es_cliente': es_cliente, 'es_proveedor': es_proveedor
        }

        # Validaciones de backend
        if not cuit_cuil:
            flash('El CUIT/CUIL es obligatorio.', 'danger')
            return render_template('personas/agregar.html', **form_data)
        if not (nombre or apellido or razon_social):
            flash('Debe ingresar un Nombre y Apellido, o una Razón Social.', 'danger')
            return render_template('personas/agregar.html', **form_data)
            
        try:
            # PARÁMETROS Y QUERY ACTUALIZADOS CON FECHA DE NACIMIENTO
            params = (nombre, apellido, razon_social, cuit_cuil, telefono, email, es_cliente, es_proveedor, fecha_nacimiento)
            query = "INSERT INTO personas (nombre, apellido, razon_social, cuit_cuil, telefono, email, es_cliente, es_proveedor, fecha_nacimiento) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            persona_id = db_execute(query, params, return_id=True)
            
            detalles = {
                'nombre_o_razon': razon_social or f"{nombre} {apellido}", 
                'cuit_cuil': cuit_cuil, 
                'roles': {'cliente': es_cliente, 'proveedor': es_proveedor},
                'fecha_nacimiento': fecha_nacimiento # Incluido en el log
            }
            
            registrar_movimiento(current_user.id, 'CREACION', 'PERSONA', persona_id, detalles)
            flash('Persona agregada exitosamente.', 'success')
            return redirect(url_for('listar_personas'))
        except sqlite3.IntegrityError:
            flash(f'Error: El CUIT/CUIL {cuit_cuil} ya existe.', 'danger')
            return render_template('personas/agregar.html', **form_data)
        except Exception as e:
            app.logger.error(f"Error al agregar persona: {e}", exc_info=True)
            flash(f'Ocurrió un error inesperado al agregar la persona: {e}', 'danger')
            return render_template('personas/agregar.html', **form_data)
    else: # GET request, carga inicial del formulario
        return render_template('personas/agregar.html',
                               nombre='', apellido='', razon_social='',
                               cuit_cuil='', telefono='', email='',
                               fecha_nacimiento='', # NUEVO CAMPO
                               es_cliente=False, es_proveedor=False)
        
        
@app.route('/personas/editar/<int:persona_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_persona(persona_id):
    persona_data_db = db_query("SELECT * FROM personas WHERE id = ?", (persona_id,))
    if not persona_data_db:
        flash("Persona no encontrada.", "danger")
        return redirect(url_for('listar_personas'))
    
    persona = dict(persona_data_db[0]) # Convertir a diccionario mutable para facilitar la actualización en caso de error POST

    if request.method == 'POST':
        # Recolectar todos los datos del formulario al principio del POST
        nombre = request.form.get('nombre', '').strip()
        apellido = request.form.get('apellido', '').strip()
        razon_social = request.form.get('razon_social', '').strip()
        cuit_cuil = request.form.get('cuit_cuil', '').strip()
        telefono = request.form.get('telefono', '').strip()
        email = request.form.get('email', '').strip()
        fecha_nacimiento = request.form.get('fecha_nacimiento', '') # NUEVO CAMPO
        es_cliente = 'es_cliente' in request.form
        es_proveedor = 'es_proveedor' in request.form

        # Actualiza el diccionario 'persona' con los datos del formulario POST
        # para que se reflejen si se vuelve a renderizar la plantilla por un error
        persona['nombre'] = nombre
        persona['apellido'] = apellido
        persona['razon_social'] = razon_social
        persona['cuit_cuil'] = cuit_cuil
        persona['telefono'] = telefono
        persona['email'] = email
        persona['fecha_nacimiento'] = fecha_nacimiento # NUEVO CAMPO
        persona['es_cliente'] = es_cliente
        persona['es_proveedor'] = es_proveedor

        # Validaciones de backend
        if not cuit_cuil:
            flash('El CUIT/CUIL es obligatorio.', 'danger')
            return render_template('personas/editar.html', persona=persona)
        if not (nombre or apellido or razon_social):
            flash('Debe ingresar un Nombre y Apellido, o una Razón Social.', 'danger')
            return render_template('personas/editar.html', persona=persona)
            
        try:
            # PARÁMETROS Y QUERY ACTUALIZADOS CON FECHA DE NACIMIENTO
            params = (nombre, apellido, razon_social, cuit_cuil, telefono, email, es_cliente, es_proveedor, fecha_nacimiento, persona_id)
            query = "UPDATE personas SET nombre=?, apellido=?, razon_social=?, cuit_cuil=?, telefono=?, email=?, es_cliente=?, es_proveedor=?, fecha_nacimiento=? WHERE id=?"
            db_execute(query, params)
            
            # Los detalles del log se basan en los datos actualizados
            original_persona_data_for_log = dict(persona_data_db[0]) 
            detalles = {
                'id': persona_id,
                'nombre_o_razon_viejo': original_persona_data_for_log['razon_social'] or f"{original_persona_data_for_log['nombre']} {original_persona_data_for_log['apellido']}",
                'nombre_o_razon_nuevo': razon_social or f"{nombre} {apellido}",
                'cuit_cuil': cuit_cuil,
                'roles_nuevos': {'cliente': es_cliente, 'proveedor': es_proveedor},
                'fecha_nacimiento': fecha_nacimiento # Incluido en el log
            }
            registrar_movimiento(current_user.id, 'MODIFICACION', 'PERSONA', persona_id, detalles)
            flash('Persona actualizada exitosamente.', 'success')
            return redirect(url_for('listar_personas'))
        except sqlite3.IntegrityError:
            flash(f'Error: El CUIT/CUIL {cuit_cuil} ya existe para otra persona.', 'danger')
            return render_template('personas/editar.html', persona=persona)
        except Exception as e:
            app.logger.error(f"Error al editar persona {persona_id}: {e}", exc_info=True)
            flash(f'Ocurrió un error inesperado al actualizar la persona: {e}', 'danger')
            return render_template('personas/editar.html', persona=persona)
    else: # GET request, carga inicial del formulario de edición
        return render_template('personas/editar.html', persona=persona)
    
    
    
    
@app.route('/personas/eliminar/<int:persona_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_persona(persona_id):
    # Verificar si la persona tiene registros asociados antes de eliminar
    if db_query("SELECT id FROM ventas WHERE cliente_id = ? LIMIT 1", (persona_id,)) or \
       db_query("SELECT id FROM servicios_reparacion WHERE cliente_id = ? LIMIT 1", (persona_id,)) or \
       db_query("SELECT id FROM compras WHERE proveedor_id = ? LIMIT 1", (persona_id,)) or \
       db_query("SELECT id FROM pagos_proveedores WHERE proveedor_id = ? LIMIT 1", (persona_id,)):
        flash('No se puede eliminar la persona porque tiene registros asociados (ventas, servicios o compras/pagos).', 'danger')
        return redirect(url_for('listar_personas'))

    persona_info = db_query("SELECT nombre, apellido, razon_social FROM personas WHERE id = ?", (persona_id,))
    if persona_info:
        detalles = {'id': persona_id, 'nombre_o_razon': persona_info[0]['razon_social'] or f"{persona_info[0]['nombre']} {persona_info[0]['apellido']}"}
        db_execute("DELETE FROM personas WHERE id=?", (persona_id,))
        registrar_movimiento(current_user.id, 'ELIMINACION', 'PERSONA', persona_id, detalles)
        flash('Persona eliminada exitosamente.', 'success')
    else:
        flash("Persona no encontrada.", "danger")
    return redirect(url_for('listar_personas'))

# =================================================================
# === MÓDULO DE INVENTARIO Y COMPRAS ==============================
# =================================================================
@app.route('/inventario/celulares')
@login_required
def inventario_celulares():
    # Solo muestra celulares con stock > 0 para simplificar la vista de "disponibles"
    lista_celulares = db_query("SELECT * FROM celulares WHERE stock > 0 ORDER BY marca, modelo, condicion")
    return render_template('inventario/celulares.html', inventario=lista_celulares)

## NUEVAS MODIFICACIONES (Punto 1 y 2 de Requerimientos) ##
## NUEVAS MODIFICACIONES (Punto 1 y 2 de Requerimientos) ##
@app.route('/compras/registrar_celular', methods=['GET', 'POST'])
@login_required
@admin_required
def registrar_compra_celular():
    # Obtenemos todas las cotizaciones disponibles del contexto
    dolar_info = inject_dolar_values()
    
    form_data = {}

    if request.method == 'POST':
        db_conn = get_db()
        try:
            # Capturamos TODO como listas para los campos dinámicos
            form_lists = request.form.to_dict(flat=False) 
            
            # --- LÓGICA DE SELECCIÓN DE DÓLAR ---
            tipo_dolar_elegido = request.form.get('tipo_dolar', 'blue')
            if tipo_dolar_elegido == 'oficial':
                valor_dolar_compra_local = dolar_info.get('valor_bcra_compra') or 1.0
            elif tipo_dolar_elegido == 'manual':
                valor_dolar_compra_local = float(request.form.get('valor_dolar_manual') or 1.0)
            else: # blue
                valor_dolar_compra_local = dolar_info.get('valor_blue_compra') or 1.0

            # Datos del Proveedor y Pagos
            proveedor_id_raw = request.form.get('proveedor_id')
            proveedor_id = int(proveedor_id_raw) if proveedor_id_raw else None
            
            monto_pago_inicial_ars = float(request.form.get('monto_pago_inicial_ars') or 0)
            monto_pago_inicial_usd = float(request.form.get('monto_pago_inicial_usd') or 0)
            entidad_pago_ars = request.form.get('cuenta_pago_ars', 'EFECTIVO')
            entidad_pago_usd = request.form.get('cuenta_origen_usd_inicial', 'EFECTIVO')
            es_parte_pago_checkbox = 'es_parte_pago_checkbox' in request.form 

            # --- CAPTURA DE LISTAS PARA EL BUCLE ---
            tipos_list = form_lists.get('tipo_item[]', [])
            imeis_list = form_lists.get('imei[]', [])
            marcas_list = form_lists.get('marca[]', [])
            modelos_list = form_lists.get('modelo[]', [])
            condiciones_list = form_lists.get('condicion[]', [])
            colores_list = form_lists.get('color[]', [])
            almacenamientos_list = form_lists.get('almacenamiento_gb[]', [])
            ram_list = form_lists.get('ram_gb[]', [])
            costos_list = form_lists.get('costo_usd[]', [])
            observaciones_list = form_lists.get('observaciones_celular[]', [])
            bateria_list = form_lists.get('bateria_salud[]', [])

            if not imeis_list:
                flash("Debe añadir al menos un celular o equipo.", "danger")
                return redirect(url_for('registrar_compra_celular'))
            
            if not es_parte_pago_checkbox and not proveedor_id: 
                flash("Por favor, seleccione un proveedor.", "danger")
                return redirect(url_for('registrar_compra_celular'))
            
            celulares_procesados = []
            db_conn.execute("BEGIN TRANSACTION")

            for i in range(len(imeis_list)):
                tipo_item_val = tipos_list[i] if i < len(tipos_list) else 'CELULAR'
                marca_val = marcas_list[i].strip() if i < len(marcas_list) else ''
                modelo_val = modelos_list[i].strip() if i < len(modelos_list) else ''
                imei_val = imeis_list[i].strip() if i < len(imeis_list) else ''
                condicion_val = "Entregado como parte de pago" if es_parte_pago_checkbox else (condiciones_list[i] if i < len(condiciones_list) else 'Nuevo')
                color_val = colores_list[i].strip() if i < len(colores_list) else ''
                
                try:
                    alm_val = int(almacenamientos_list[i]) if i < len(almacenamientos_list) and almacenamientos_list[i] else 0
                    # RAM es opcional: si está vacío se guarda como 0
                    ram_val = int(ram_list[i]) if i < len(ram_list) and ram_list[i] else 0
                    costo_u_usd = float(costos_list[i]) if i < len(costos_list) and costos_list[i] else 0.0
                    bat_val = int(bateria_list[i]) if i < len(bateria_list) and bateria_list[i] and bateria_list[i].isdigit() else None
                    # Observaciones es opcional
                    texto_obs = observaciones_list[i].strip() if i < len(observaciones_list) else ''
                    obs_val = f"[{tipo_item_val}] {texto_obs}".strip()
                except ValueError:
                    db_conn.rollback()
                    flash(f"Error de formato numérico en ítem #{i+1}. Revise memorias y costos.", "danger")
                    return redirect(url_for('registrar_compra_celular'))

                # --- VALIDACIÓN DE CAMPOS OBLIGATORIOS ---
                if not marca_val or not modelo_val or not imei_val or not color_val or alm_val <= 0 or costo_u_usd <= 0:
                    db_conn.rollback()
                    flash(f"Error en ítem #{i+1}: Marca, Modelo, IMEI, Color, Almacenamiento y Costo son obligatorios.", "danger")
                    return redirect(url_for('registrar_compra_celular'))
                
                # Validar stock
                existing = db_query_func(db_conn, "SELECT id, stock FROM celulares WHERE imei = ?", (imei_val,))
                if existing and existing[0]['stock'] == 1:
                    db_conn.rollback()
                    flash(f'El IMEI/SN "{imei_val}" ya está disponible en stock.', 'danger')
                    return redirect(url_for('registrar_compra_celular'))

                stock_db = 0 if es_parte_pago_checkbox else 1
                pp_db = 1 if es_parte_pago_checkbox else 0

                if existing:
                    celular_id = existing[0]['id']
                    db_execute_func(db_conn, "UPDATE celulares SET stock=?, es_parte_pago=?, marca=?, modelo=?, condicion=?, almacenamiento_gb=?, ram_gb=?, color=?, bateria_salud=?, costo_usd=?, observaciones=? WHERE id=?",
                                    (stock_db, pp_db, marca_val, modelo_val, condicion_val, alm_val, ram_val, color_val, bat_val, costo_u_usd, obs_val, celular_id))
                else:
                    celular_id = db_execute_func(db_conn, "INSERT INTO celulares (marca, modelo, imei, condicion, almacenamiento_gb, ram_gb, color, bateria_salud, costo_usd, stock, observaciones, es_parte_pago) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                                                (marca_val, modelo_val, imei_val, condicion_val, alm_val, ram_val, color_val, bat_val, costo_u_usd, stock_db, obs_val, pp_db), return_id=True)

                if not es_parte_pago_checkbox:
                    costo_total_ars_informativo = costo_u_usd * valor_dolar_compra_local
                    
                    compra_id = db_execute_func(db_conn,
                        "INSERT INTO compras (proveedor_id, user_id, fecha_compra, tipo_item, item_id, imei_celular, cantidad, costo_unitario_usd, costo_total_usd, valor_dolar_momento, costo_total_ars, estado_pago) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE')",
                        (proveedor_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tipo_item_val, celular_id, imei_val, 1, costo_u_usd, costo_u_usd, valor_dolar_compra_local, costo_total_ars_informativo),
                        return_id=True
                    )
                    celulares_procesados.append({'compra_id': compra_id, 'costo_total_usd': costo_u_usd})

            # --- IMPACTO FINANCIERO (SOLUCIÓN: STRICTLY USD PARA EQUIPOS) ---
            if not es_parte_pago_checkbox and celulares_procesados and (monto_pago_inicial_ars > 0 or monto_pago_inicial_usd > 0):
                
                # 1. Calculamos el impacto real en USD (Billete + Conversión de pesos)
                cotiz = valor_dolar_compra_local if valor_dolar_compra_local > 0 else 1.0
                usd_cubiertos_con_pesos = monto_pago_inicial_ars / cotiz
                total_usd_a_descontar_deuda = monto_pago_inicial_usd + usd_cubiertos_con_pesos
                
                m_disponible_usd = total_usd_a_descontar_deuda

                # 2. DISTRIBUCIÓN FIFO: Repartimos ese pago entre los equipos comprados
                for item in celulares_procesados:
                    if m_disponible_usd <= 0.001: break 
                    
                    deuda_item_usd = item['costo_total_usd']
                    pago_usd_para_este_item = min(m_disponible_usd, deuda_item_usd)

                    # REGISTRO VINCULADO: Seteamos monto_ars en 0 para evitar duplicación en los saldos USD
                    db_execute_func(db_conn, """
                        INSERT INTO pagos_proveedores (proveedor_id, user_id, fecha_pago, compra_id, monto_ars, monto_usd, valor_dolar_momento, tipo_pago, referencia, imputacion) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EQUIPOS')
                    """, (proveedor_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                          item['compra_id'], 0, pago_usd_para_este_item, 
                          cotiz, entidad_pago_ars, 
                          f"Pago inicial equipo - Cotiz: {cotiz}"))

                    status = 'PAGADO_TOTAL' if pago_usd_para_este_item >= (deuda_item_usd - 0.001) else 'PAGADO_PARCIAL'
                    db_execute_func(db_conn, "UPDATE compras SET estado_pago = ? WHERE id = ?", (status, item['compra_id']))
                    
                    m_disponible_usd -= pago_usd_para_este_item

                # 3. REGISTRO FÍSICO DE CAJA
                if monto_pago_inicial_ars > 0:
                    registrar_movimiento_caja(current_user.id, 'EGRESO_PAGO_PROVEEDOR_ARS', monto_ars=monto_pago_inicial_ars, 
                                              descripcion=f"Pago inicial {len(celulares_procesados)} equipos (Salida ARS)", 
                                              metodo_pago=entidad_pago_ars)
                
                if monto_pago_inicial_usd > 0:
                    registrar_movimiento_caja(current_user.id, 'EGRESO_PAGO_PROVEEDOR_USD', monto_usd=monto_pago_inicial_usd, 
                                              descripcion=f"Pago inicial {len(celulares_procesados)} equipos (Salida USD)", 
                                              metodo_pago=entidad_pago_usd)

            db_conn.commit()
            flash('Compra de equipos registrada con éxito. Pagos vinculados en USD.', 'success')
            return redirect(url_for('listar_compras'))
            
        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error compra equipo: {e}", exc_info=True)
            flash(f"Error: {e}", "danger")
            return redirect(url_for('registrar_compra_celular'))
    
    proveedores = db_query("SELECT * FROM personas WHERE es_proveedor = 1 ORDER BY razon_social, apellido")
    return render_template('compras/registrar_compra.html', proveedores=proveedores, item_type='celular', form_data=form_data)



@app.route('/compras/registrar_repuesto', methods=['GET', 'POST'])
@login_required
#@admin_required
@tecnico_required
def registrar_compra_repuesto():
    # Obtenemos las cotizaciones del contexto
    dolar_info = inject_dolar_values()
    form_data = {}

    if request.method == 'POST':
        db_conn = get_db()
        try:
            # 1. CAPTURA DE MONEDA Y COTIZACIÓN
            moneda_compra = request.form.get('moneda_compra', 'USD') # 'ARS' o 'USD'
            tipo_dolar_elegido = request.form.get('tipo_dolar', 'blue')
            
            if tipo_dolar_elegido == 'oficial':
                valor_dolar_compra_local = dolar_info.get('valor_bcra_compra') or 1.0
            elif tipo_dolar_elegido == 'manual':
                valor_dolar_compra_local = float(request.form.get('valor_dolar_manual') or 1.0)
            else: # blue
                valor_dolar_compra_local = dolar_info.get('valor_blue_compra') or 1.0

            # 2. CAPTURA DE DATOS BÁSICOS
            proveedor_id = int(request.form.get('proveedor_id') or 0)
            categoria = request.form.get('categoria', 'REPUESTO').strip()
            nombre_parte = request.form.get('nombre_parte', '').strip()
            modelo_compatible = request.form.get('modelo_compatible', '').strip() or 'Universal'
            cantidad_ingresada = int(request.form.get('stock') or 0)
            costo_ingresado = float(request.form.get('costo_unidad') or 0) # El precio que el usuario escribe
            
            # --- LÓGICA DE CONVERSIÓN DE MONEDA ---
            if moneda_compra == 'ARS':
                # Si compró en pesos, calculamos cuánto es en USD para el inventario
                costo_unitario_usd = costo_ingresado / valor_dolar_compra_local
                costo_total_ars = costo_ingresado * cantidad_ingresada
                costo_total_usd = costo_unitario_usd * cantidad_ingresada
            else:
                # Si compró en USD, el costo unitario es el ingresado
                costo_unitario_usd = costo_ingresado
                costo_total_usd = costo_unitario_usd * cantidad_ingresada
                costo_total_ars = costo_total_usd * valor_dolar_compra_local

            if not proveedor_id or not nombre_parte or cantidad_ingresada <= 0:
                flash("Proveedor, nombre y cantidad son obligatorios.", "danger")
                return redirect(url_for('registrar_compra_repuesto'))

            db_conn.execute("BEGIN TRANSACTION")

            # 3. ACTUALIZAR STOCK Y COSTO PROMEDIO EN INVENTARIO
            # NORMALIZAMOS los datos para evitar errores de duplicado por espacios o minúsculas
            nombre_parte = request.form.get('nombre_parte', '').strip().upper()
            modelo_compatible = (request.form.get('modelo_compatible', '') or 'Universal').strip().upper()
            categoria = request.form.get('categoria', 'REPUESTO').strip().upper()

            # BUSCAMOS si existe por nombre y modelo (que es la restricción UNIQUE de tu DB)
            # Eliminamos la categoría de la búsqueda para que no falle si se categorizó distinto antes
            existing_part = db_query_func(db_conn, 
                "SELECT id, stock, costo_usd FROM repuestos WHERE nombre_parte = ? AND modelo_compatible = ?", 
                (nombre_parte, modelo_compatible))
            
            if existing_part:
                # SI EXISTE: Hacemos UPDATE
                part = existing_part[0]
                repuesto_id = part['id']
                nuevo_stock = part['stock'] + cantidad_ingresada
                
                # Promedio ponderado del costo en USD
                costo_actual = part['costo_usd'] if part['costo_usd'] else 0
                costo_promedio_usd = ((part['stock'] * costo_actual) + (cantidad_ingresada * costo_unitario_usd)) / nuevo_stock
                
                db_execute_func(db_conn, 
                    "UPDATE repuestos SET stock = ?, costo_usd = ?, categoria = ? WHERE id = ?", 
                    (nuevo_stock, costo_promedio_usd, categoria, repuesto_id))
            else:
                # SI NO EXISTE: Hacemos INSERT
                repuesto_id = db_execute_func(db_conn, 
                    "INSERT INTO repuestos (nombre_parte, modelo_compatible, costo_usd, stock, categoria, precio_venta_ars, precio_venta_usd) VALUES (?, ?, ?, ?, ?, 0.0, 0.0)", 
                    (nombre_parte, modelo_compatible, costo_unitario_usd, cantidad_ingresada, categoria), return_id=True)
                
            # 4. REGISTRAR LA COMPRA
            compra_id = db_execute_func(db_conn,
                """INSERT INTO compras (proveedor_id, user_id, fecha_compra, tipo_item, item_id, cantidad, 
                costo_unitario_usd, costo_total_usd, valor_dolar_momento, costo_total_ars, estado_pago) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDIENTE')""",
                (proveedor_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), categoria, repuesto_id, 
                 cantidad_ingresada, costo_unitario_usd, costo_total_usd, valor_dolar_compra_local, costo_total_ars),
                return_id=True
            )

           # 5. LÓGICA DE PAGO INICIAL (ACTUALIZADA: RESTRICCIÓN PARA TÉCNICOS)
            if current_user.role == 'tecnico':
                # Si es técnico, forzamos valores en 0 y estado Pendiente
                monto_pago_ars = 0.0
                monto_pago_usd = 0.0
                estado_pago_form = 'PENDIENTE'
            else:
                # Si es administrador, capturamos los datos del formulario normalmente
                monto_pago_ars = float(request.form.get('monto_pago_inicial_ars') or 0)
                monto_pago_usd = float(request.form.get('monto_pago_inicial_usd') or 0)
                estado_pago_form = request.form.get('estado_pago', 'PENDIENTE')

            # Esta lógica solo se ejecuta si el estado NO es pendiente y hay montos (Solo para Admins)
            if estado_pago_form != 'PENDIENTE' and (monto_pago_ars > 0 or monto_pago_usd > 0):
                # CAPTURA DE CUENTAS DINÁMICAS DESDE EL FORMULARIO
                cuenta_ars_elegida = request.form.get('cuenta_pago_ars', 'EFECTIVO')
                cuenta_usd_elegida = request.form.get('cuenta_origen_usd_inicial', 'EFECTIVO')
                
                # Impacto contable del pago
                monto_contable_descuento = monto_pago_ars + (monto_pago_usd * valor_dolar_compra_local)

                # Registrar en la tabla pagos_proveedores usando la cuenta de pesos como referencia de tipo_pago
                pago_id = db_execute_func(db_conn, """
                    INSERT INTO pagos_proveedores (proveedor_id, user_id, fecha_pago, compra_id, monto_ars, monto_usd, tipo_pago, referencia, imputacion) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'REPUESTOS')
                """, (proveedor_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), compra_id, 
                      monto_contable_descuento, monto_pago_usd, cuenta_ars_elegida, f"Compra en {moneda_compra} - Cotiz: {valor_dolar_compra_local}"))

                # --- REGISTROS DE CAJA DINÁMICOS ---
                if monto_pago_ars > 0:
                    # Registramos el egreso de pesos en la cuenta específica elegida
                    registrar_movimiento_caja(current_user.id, 'PAGO_PROVEEDOR_ARS', 
                                              monto_ars=monto_pago_ars, 
                                              descripcion=f"Pago {categoria} ({moneda_compra})", 
                                              referencia_id=pago_id, 
                                              metodo_pago=cuenta_ars_elegida)
                
                if monto_pago_usd > 0:
                    # Registramos el egreso de dólares en la cuenta específica elegida
                    registrar_movimiento_caja(current_user.id, 'PAGO_PROVEEDOR_USD', 
                                              monto_usd=monto_pago_usd, 
                                              descripcion=f"Pago {categoria} USD", 
                                              referencia_id=pago_id, 
                                              metodo_pago=cuenta_usd_elegida)
                
                # Actualizar el estado de la compra (Liquidación)
                status_final = 'PAGADO_TOTAL' if monto_contable_descuento >= (costo_total_ars - 0.05) else 'PAGADO_PARCIAL'
                db_execute_func(db_conn, "UPDATE compras SET estado_pago = ? WHERE id = ?", (status_final, compra_id))
            else:
                # Si es técnico o el Admin eligió PENDIENTE, nos aseguramos de que el estado en DB sea PENDIENTE
                db_execute_func(db_conn, "UPDATE compras SET estado_pago = 'PENDIENTE' WHERE id = ?", (compra_id,))

            db_conn.commit()
            flash(f'Compra registrada exitosamente en {moneda_compra} (Estado: {estado_pago_form}).', 'success')
            return redirect(url_for('listar_compras'))
            
        except Exception as e:
            db_conn.rollback()
            app.logger.error(f'Error compra repuesto: {e}', exc_info=True)
            flash(f'Error: {e}', 'danger')
            return redirect(url_for('registrar_compra_repuesto'))

    proveedores = db_query("SELECT * FROM personas WHERE es_proveedor = 1 ORDER BY razon_social, apellido")
    return render_template('compras/registrar_compra.html', proveedores=proveedores, item_type='repuesto', form_data=form_data)



@app.route('/inventario/celulares/editar/<int:celular_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_celular(celular_id):
    celular = db_query("SELECT * FROM celulares WHERE id = ?", (celular_id,))
    if not celular:
        flash("Celular no encontrado.", "danger")
        return redirect(url_for('inventario_celulares'))
    celular = celular[0]

    if request.method == 'POST':
        try:
            marca = request.form['marca'].strip()
            modelo = request.form['modelo'].strip()
            imei = request.form['imei'].strip()
            condicion = request.form['condicion']
            almacenamiento_gb = int(request.form['almacenamiento_gb'])
            ram_gb = int(request.form['ram_gb'])
            color = request.form['color'].strip()
            bateria_salud = int(request.form.get('bateria_salud', 0)) if condicion == 'Usado' else None
            observaciones = request.form.get('observaciones', '').strip()
            costo_usd = float(request.form['costo_usd'])
            stock = int(request.form.get('stock', 1))
            # es_parte_pago NO se modifica directamente desde aquí. Se mantiene su estado actual.

            if not all([marca, modelo, imei, condicion, almacenamiento_gb, ram_gb, color, costo_usd]):
                flash("Todos los campos obligatorios deben ser completados.", "danger")
                return redirect(url_for('editar_celular', celular_id=celular_id))
            if costo_usd <= 0:
                flash("El costo en USD debe ser positivo.", "danger")
                return redirect(url_for('editar_celular', celular_id=celular_id))
            if not (imei.isdigit() and len(imei) == 15):
                flash("El IMEI debe contener 15 dígitos numéricos.", "danger")
                return redirect(url_for('editar_celular', celular_id=celular_id))
            if stock not in [0, 1]:
                flash("El stock de un celular debe ser 0 (vendido) o 1 (disponible).", "danger")
                return redirect(url_for('editar_celular', celular_id=celular_id))

            # Verificar if el nuevo IMEI ya existe para otro celular
            existing_imei = db_query("SELECT id FROM celulares WHERE imei = ? AND id != ?", (imei, celular_id))
            if existing_imei:
                flash(f'Error: El IMEI "{imei}" ya está registrado para otro celular.', 'danger')
                return redirect(url_for('editar_celular', celular_id=celular_id))

            db_execute("UPDATE celulares SET marca=?, modelo=?, imei=?, condicion=?, almacenamiento_gb=?, ram_gb=?, color=?, bateria_salud=?, observaciones=?, costo_usd=?, stock=? WHERE id=?",
                       (marca, modelo, imei, condicion, almacenamiento_gb, ram_gb, color, bateria_salud, observaciones, costo_usd, stock, celular_id))
            
            detalles = {'id': celular_id, 'marca': marca, 'modelo': modelo, 'imei': imei, 'stock_nuevo': stock}
            registrar_movimiento(current_user.id, 'MODIFICACION', 'CELULAR', celular_id, detalles)
            
            flash('Celular actualizado exitosamente.', 'success')
            return redirect(url_for('inventario_celulares'))
        except sqlite3.IntegrityError as e:
            flash(f'Error de integridad: {e}', 'danger')
            return redirect(url_for('editar_celular', celular_id=celular_id))
        except ValueError as e:
            flash(f'Error de validación: {e}', 'danger')
            return redirect(url_for('editar_celular', celular_id=celular_id))
        except Exception as e:
            app.logger.error(f"Error al editar celular {celular_id}: {e}", exc_info=True)
            flash(f'Ocurrió un error inesperado al actualizar el celular: {e}', "danger")
            return redirect(url_for('editar_celular', celular_id=celular_id))
    
    return render_template('inventario/editar_celular.html', celular=celular)


@app.route('/inventario/repuestos/editar/<int:repuesto_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_repuesto(repuesto_id):
    repuesto = db_query("SELECT * FROM repuestos WHERE id = ?", (repuesto_id,))
    if not repuesto:
        flash("Producto no encontrado.", "danger")
        return redirect(url_for('inventario_repuestos'))
    repuesto = repuesto[0] # Acceder a los datos del repuesto como un diccionario

    if request.method == 'POST':
        try:
            nombre_parte = request.form['nombre_parte'].strip()
            modelo_compatible = request.form.get('modelo_compatible', '').strip()
            # --- NUEVO: Captura de Categoría ---
            categoria = request.form.get('categoria', 'REPUESTO').strip()
            
            costo_usd = float(request.form['costo_usd'])
            stock = int(request.form['stock'])
            
            # REMOVIDO: Ya no se toman 'precio_venta_ars' ni 'precio_venta_usd' del formulario POST aquí.

            if not all([nombre_parte, categoria]):
                flash("El nombre y la categoría son obligatorios.", "danger")
                return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))
            
            # Permitimos costo 0 (ej. recuperados), pero no negativo
            if costo_usd < 0:
                flash("El costo en USD no puede ser negativo.", "danger")
                return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))
            
            if stock < 0:
                flash("El stock no puede ser negativo.", "danger")
                return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))

            # Verificar si el nuevo nombre/modelo/categoría ya existe para otro producto
            # Se agrega 'categoria' a la validación de unicidad
            existing_part = db_query("SELECT id FROM repuestos WHERE nombre_parte = ? AND modelo_compatible = ? AND categoria = ? AND id != ?", (nombre_parte, modelo_compatible, categoria, repuesto_id))
            if existing_part:
                flash(f'Error: Ya existe un producto con el nombre "{nombre_parte}", modelo "{modelo_compatible}" y categoría "{categoria}".', 'danger')
                return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))

            # MODIFICADO: Se incluye 'categoria' en el UPDATE
            db_execute("UPDATE repuestos SET nombre_parte=?, modelo_compatible=?, categoria=?, costo_usd=?, stock=? WHERE id=?",
                       (nombre_parte, modelo_compatible, categoria, costo_usd, stock, repuesto_id))
            
            # MODIFICADO: Detalles del log actualizados
            detalles = {
                'id': repuesto_id, 
                'nombre_parte': nombre_parte, 
                'modelo_compatible': modelo_compatible, 
                'categoria_nueva': categoria,
                'stock_nuevo': stock, 
                'costo_usd_nuevo': costo_usd
            }
            # Cambiamos 'REPUESTO' por 'PRODUCTO' para generalizar
            registrar_movimiento(current_user.id, 'MODIFICACION', 'PRODUCTO', repuesto_id, detalles)
            
            flash('Producto actualizado exitosamente.', 'success')
            return redirect(url_for('inventario_repuestos'))
        except sqlite3.IntegrityError as e:
            flash(f'Error de integridad: {e}', 'danger')
            return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))
        except ValueError as e:
            flash(f'Error de validación: {e}', 'danger')
            return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))
        except Exception as e:
            app.logger.error(f"Error al editar producto {repuesto_id}: {e}", exc_info=True)
            flash(f'Ocurrió un error inesperado al actualizar: {e}', "danger")
            return redirect(url_for('editar_repuesto', repuesto_id=repuesto_id))
    
    # En el GET request, `repuesto` ya contiene los datos para mostrar
    return render_template('inventario/editar_repuesto.html', repuesto=repuesto)



# --- Nueva Ruta para Lista de Precios de Repuestos ---
@app.route('/inventario/repuestos/lista_precios', methods=['GET', 'POST'])
@login_required
@admin_required
@tecnico_required
def lista_precios_repuestos():
    dolar_info_from_context = inject_dolar_values()
    valor_dolar_compra_local = dolar_info_from_context['valor_dolar_compra'] or 1.0
    valor_dolar_venta_local = dolar_info_from_context['valor_dolar_venta'] or 1.0

    # Captura de IDs seleccionados desde el filtro Select2
    selected_repuestos_ids_str = request.args.get('repuesto_ids') or request.form.get('selected_repuesto_ids_hidden')
    selected_repuesto_ids = [int(x) for x in selected_repuestos_ids_str.split(',') if x.isdigit()] if selected_repuestos_ids_str else []

    # --- CONSULTA MEJORADA: Obtenemos el repuesto y los datos de su ÚLTIMA COMPRA ---
    # Esto trae el valor_dolar_momento y el costo_total_ars real de la transacción
    query_repuestos = """
        SELECT r.*, 
               c.valor_dolar_momento as dolar_compra_historico, 
               c.costo_unitario_usd as costo_usd_compra,
               (c.costo_total_ars / c.cantidad) as costo_ars_real_unitario
        FROM repuestos r
        LEFT JOIN (
            SELECT item_id, tipo_item, valor_dolar_momento, costo_unitario_usd, costo_total_ars, cantidad
            FROM compras 
            WHERE id IN (SELECT MAX(id) FROM compras WHERE tipo_item IN ('REPUESTO', 'ACCESORIO', 'EQUIPO') GROUP BY item_id, tipo_item)
        ) c ON r.id = c.item_id AND r.categoria = c.tipo_item
    """
    
    query_params = []
    if selected_repuesto_ids:
        placeholders = ','.join('?' * len(selected_repuesto_ids))
        query_repuestos += f" WHERE r.id IN ({placeholders})"
        query_params.extend(selected_repuesto_ids)
    
    query_repuestos += " ORDER BY r.nombre_parte"
    repuestos_raw = db_query(query_repuestos, tuple(query_params))

    repuestos_con_precios = []

    # Inicializar datos del formulario para la plantilla
    form_data_for_template = {
        'pricing_strategy': request.form.get('pricing_strategy', 'porcentaje'),
        'ganancia_pct': float(request.form.get('ganancia_pct', 30) or 30),
        'monto_fijo': float(request.form.get('monto_fijo', 0) or 0),
        'monto_fijo_moneda': request.form.get('monto_fijo_moneda', 'USD'),
        'selected_repuesto_ids': selected_repuestos_ids_str or '' 
    }

    if request.method == 'POST':
        action_type = request.form.get('action_type')

        if action_type == 'save_prices':
            db_conn = get_db()
            try:
                db_conn.execute("BEGIN TRANSACTION")
                updated_count = 0
                for rep in repuestos_raw: 
                    rep_id = rep['id']
                    p_ars = float(request.form.get(f'precio_venta_ars_{rep_id}') or 0)
                    if p_ars >= 0:
                        p_usd = p_ars / valor_dolar_venta_local if valor_dolar_venta_local else 0
                        db_execute_func(db_conn, "UPDATE repuestos SET precio_venta_ars = ?, precio_venta_usd = ? WHERE id = ?", (p_ars, p_usd, rep_id))
                        updated_count += 1
                db_conn.commit()
                flash(f"{updated_count} precios actualizados correctamente.", "success")
                return redirect(url_for('imprimir_precios_repuestos', repuesto_ids=selected_repuestos_ids_str))
            except Exception as e:
                db_conn.rollback()
                flash(f"Error al guardar: {e}", "danger")

        elif action_type == 'generate_suggestions':
            for rep in repuestos_raw:
                # Usamos el costo USD de la compra si existe, sino el de la ficha del repuesto
                costo_base_usd = rep['costo_usd_compra'] if rep['costo_usd_compra'] else rep['costo_usd']
                precio_sugerido_usd = costo_base_usd

                if form_data_for_template['pricing_strategy'] == 'porcentaje':
                    precio_sugerido_usd = costo_base_usd * (1 + form_data_for_template['ganancia_pct'] / 100)
                elif form_data_for_template['pricing_strategy'] == 'monto_fijo':
                    if form_data_for_template['monto_fijo_moneda'] == 'USD':
                        precio_sugerido_usd = costo_base_usd + form_data_for_template['monto_fijo']
                    else: # ARS
                        precio_sugerido_usd = costo_base_usd + (form_data_for_template['monto_fijo'] / valor_dolar_venta_local)

                precio_sugerido_ars = precio_sugerido_usd * valor_dolar_venta_local
                
                rep_dict = dict(rep)
                # El costo ARS es el real de la compra (histórico)
                rep_dict['costo_ars'] = round(rep['costo_ars_real_unitario'] if rep['costo_ars_real_unitario'] else (costo_base_usd * valor_dolar_compra_local), 2)
                rep_dict['precio_venta_sugerido_ars'] = round(precio_sugerido_ars, 2)
                rep_dict['precio_venta_sugerido_usd'] = round(precio_sugerido_usd, 2)
                repuestos_con_precios.append(rep_dict)
            flash("Sugerencias generadas según costos históricos.", "info")

    # Si no hay acción o es un GET inicial, poblamos con los datos actuales
    if not repuestos_con_precios: 
        for rep in repuestos_raw:
            rep_dict = dict(rep)
            costo_base_usd = rep['costo_usd_compra'] if rep['costo_usd_compra'] else rep['costo_usd']
            # Costo ARS: Prioriza el real pagado en la última compra
            rep_dict['costo_ars'] = round(rep['costo_ars_real_unitario'] if rep['costo_ars_real_unitario'] else (costo_base_usd * valor_dolar_compra_local), 2)
            rep_dict['precio_venta_sugerido_ars'] = round(rep['precio_venta_ars'], 2)
            rep_dict['precio_venta_sugerido_usd'] = round(rep['precio_venta_usd'], 2)
            repuestos_con_precios.append(rep_dict)

    all_repuestos_for_select2 = db_query("SELECT id, nombre_parte, modelo_compatible, stock, costo_usd FROM repuestos ORDER BY nombre_parte")

    return render_template('inventario/lista_precios_repuestos.html',
                           repuestos=repuestos_con_precios,
                           valor_dolar_compra=valor_dolar_compra_local,
                           valor_dolar_venta=valor_dolar_venta_local,
                           form_data=form_data_for_template,
                           all_repuestos_for_select2=all_repuestos_for_select2)
    
    
    
@app.route('/inventario/repuestos/imprimir_precios')
@login_required
@admin_required
def imprimir_precios_repuestos():
    # Retrieve selected repuesto IDs from URL parameter
    selected_repuestos_ids_str = request.args.get('repuesto_ids')
    selected_repuesto_ids = []
    if selected_repuestos_ids_str:
        selected_repuesto_ids = [int(x) for x in selected_repuestos_ids_str.split(',') if x.isdigit()]

    # Fetch dollar values for display
    dolar_info_from_context = inject_dolar_values()
    valor_dolar_compra_local = dolar_info_from_context['valor_dolar_compra'] 
    valor_dolar_venta_local = dolar_info_from_context['valor_dolar_venta']
    
    if valor_dolar_compra_local is None or valor_dolar_compra_local == 0:
        valor_dolar_compra_local = 1.0 
    if valor_dolar_venta_local is None or valor_dolar_venta_local == 0:
        valor_dolar_venta_local = 1.0 

    query_repuestos = "SELECT id, nombre_parte, modelo_compatible, stock, costo_usd, precio_venta_ars, precio_venta_usd FROM repuestos"
    query_params_repuestos = []
    if selected_repuesto_ids:
        query_placeholders = ','.join('?' * len(selected_repuesto_ids))
        query_repuestos += f" WHERE id IN ({query_placeholders})"
        query_params_repuestos.extend(selected_repuesto_ids)
    query_repuestos += " ORDER BY nombre_parte"

    repuestos_raw = db_query(query_repuestos, tuple(query_params_repuestos))
    repuestos_para_imprimir = []

    for rep in repuestos_raw:
        rep_dict = dict(rep)
        rep_dict['costo_ars'] = round(rep['costo_usd'] * valor_dolar_compra_local, 2)
        rep_dict['precio_venta_ars'] = round(rep['precio_venta_ars'], 2)
        rep_dict['precio_venta_usd'] = round(rep['precio_venta_usd'], 2)
        repuestos_para_imprimir.append(rep_dict)

    fecha_impresion = datetime.now()

    return render_template('inventario/imprimir_precios_repuestos.html',
                           repuestos=repuestos_para_imprimir,
                           valor_dolar_compra=valor_dolar_compra_local,
                           valor_dolar_venta=valor_dolar_venta_local,
                           fecha_impresion=fecha_impresion,
                           selected_repuesto_ids_str=selected_repuestos_ids_str # Pass this back for 'Volver' button
                           )


@app.route('/inventario/celulares/eliminar/<int:celular_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_celular(celular_id):
    # Verificar if el celular tiene ventas asociadas
    if db_query("SELECT id FROM ventas WHERE celular_id = ? LIMIT 1", (celular_id,)) or \
       db_query("SELECT id FROM ventas WHERE celular_parte_pago_id = ? LIMIT 1", (celular_id,)):
        flash('No se puede eliminar el celular porque tiene ventas asociadas o fue recibido como parte de pago.', 'danger')
        return redirect(url_for('inventario_celulares'))
    
    celular_info = db_query("SELECT marca, modelo, imei FROM celulares WHERE id = ?", (celular_id,))
    if celular_info:
        detalles = {'id': celular_id, 'marca': celular_info[0]['marca'], 'modelo': celular_info[0]['modelo'], 'imei': celular_info[0]['imei']}
        db_execute("DELETE FROM celulares WHERE id=?", (celular_id,))
        registrar_movimiento(current_user.id, 'ELIMINACION', 'CELULAR', celular_id, detalles)
        flash('Celular eliminado exitosamente.', 'success')
    else:
        flash("Celular no encontrado.", "danger")
    return redirect(url_for('inventario_celulares'))

@app.route('/inventario/repuestos/eliminar/<int:repuesto_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_repuesto(repuesto_id):
    # Verificar if el repuesto tiene movimientos asociados (repuestos_usados, compras)
    if db_query("SELECT id FROM repuestos_usados WHERE repuesto_id = ? LIMIT 1", (repuesto_id,)) or \
       db_query("SELECT id FROM compras WHERE tipo_item = 'REPUESTO' AND item_id = ? LIMIT 1", (repuesto_id,)):
        flash('No se puede eliminar el repuesto porque tiene movimientos asociados (usado en servicios o compras).', 'danger')
        return redirect(url_for('inventario_repuestos'))

    repuesto_info = db_query("SELECT nombre_parte, modelo_compatible FROM repuestos WHERE id = ?", (repuesto_id,))
    if repuesto_info:
        detalles = {'id': repuesto_id, 'nombre_parte': repuesto_info[0]['nombre_parte'], 'modelo_compatible': repuesto_info[0]['modelo_compatible']}
        db_execute("DELETE FROM repuestos WHERE id=?", (repuesto_id,))
        registrar_movimiento(current_user.id, 'ELIMINACION', 'REPUESTO', repuesto_id, detalles)
        flash('Repuesto eliminado exitosamente.', 'success')
    else:
        flash("Repuesto no encontrado.", "danger")
    return redirect(url_for('inventario_repuestos'))

# --- Rutas de Compras ---
## NUEVAS MODIFICACIONES ##
@app.route('/compras')
@login_required
#@admin_required
@tecnico_required
def listar_compras():
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_proveedor = request.args.get('proveedor', '')
    filtro_tipo_item = request.args.get('tipo_item', '')
    filtro_estado_pago = request.args.get('estado_pago', '')

    query = """
        SELECT co.*, p.razon_social, p.nombre, p.apellido, u.username,
               CASE
                   WHEN co.tipo_item = 'CELULAR' THEN c.marca || ' ' || c.modelo || ' (IMEI: ' || COALESCE(c.imei, 'N/A') || ')'
                   WHEN co.tipo_item = 'REPUESTO' THEN r.nombre_parte || ' (' || COALESCE(r.modelo_compatible, 'Genérico') || ')'
                   ELSE 'Desconocido'
               END AS item_descripcion
        FROM compras co
        JOIN personas p ON co.proveedor_id = p.id
        JOIN users u ON co.user_id = u.id
        LEFT JOIN celulares c ON co.item_id = c.id AND co.tipo_item = 'CELULAR'
        LEFT JOIN repuestos r ON co.item_id = r.id AND co.tipo_item = 'REPUESTO'
        WHERE co.fecha_compra BETWEEN ? AND ?
    """
    params = [start_date, end_date_query]

    if filtro_proveedor:
        query += " AND p.id = ?"
        params.append(filtro_proveedor)
    if filtro_tipo_item:
        query += " AND co.tipo_item = ?"
        params.append(filtro_tipo_item)
    if filtro_estado_pago:
        query += " AND co.estado_pago = ?"
        params.append(filtro_estado_pago)

    query += " ORDER BY co.fecha_compra DESC"
    compras = db_query(query, tuple(params))
    
    proveedores_disponibles = db_query("SELECT id, nombre, apellido, razon_social FROM personas WHERE es_proveedor = 1 ORDER BY razon_social, apellido")

    return render_template('compras/listar_compras.html', compras=compras, start_date=start_date, end_date=end_date_display,
                           filtros_activos={'proveedor': filtro_proveedor, 'tipo_item': filtro_tipo_item, 'estado_pago': filtro_estado_pago},
                           proveedores_disponibles=proveedores_disponibles)

# --- Rutas para Cuentas Corrientes de Proveedores ---
## NUEVAS MODIFICACIONES (Punto 3 de Requerimientos) ##
@app.route('/cuentas_corrientes/proveedores')
@login_required
@admin_required
def listar_proveedores_cc():
    proveedores = db_query("SELECT id, nombre, apellido, razon_social FROM personas WHERE es_proveedor = 1 ORDER BY razon_social, apellido, nombre")
    estados_cuenta = []
    lista_equipos_tipos = "('CELULAR', 'TABLET', 'SMARTWATCH', 'EQUIPO')"

    for prov in proveedores:
        # --- SALDO USD (Equipos) ---
        # Sumamos el costo total de las facturas en USD
        compra_usd = db_query(f"SELECT COALESCE(SUM(costo_total_usd), 0.0) FROM compras WHERE proveedor_id = ? AND tipo_item IN {lista_equipos_tipos}", (prov['id'],))[0][0]
        
        # Sumamos los abonos realizados. Al ser "Strictly USD", solo miramos monto_usd 
        # (El registro se encargará de convertir los pesos a esta columna)
        pago_usd = db_query("""
            SELECT COALESCE(SUM(monto_usd), 0.0) 
            FROM pagos_proveedores 
            WHERE proveedor_id = ? AND imputacion = 'EQUIPOS' AND compra_id IS NOT NULL
        """, (prov['id'],))[0][0]
        
        saldo_usd = compra_usd - pago_usd

        # --- SALDO ARS (Repuestos) ---
        compra_ars = db_query(f"SELECT COALESCE(SUM(costo_total_ars), 0.0) FROM compras WHERE proveedor_id = ? AND tipo_item NOT IN {lista_equipos_tipos}", (prov['id'],))[0][0]
        
        # Para repuestos, la moneda base es ARS
        pago_ars = db_query("""
            SELECT COALESCE(SUM(monto_ars), 0.0) 
            FROM pagos_proveedores 
            WHERE proveedor_id = ? AND imputacion = 'REPUESTOS' AND compra_id IS NOT NULL
        """, (prov['id'],))[0][0]
        
        saldo_ars = compra_ars - pago_ars

        if abs(saldo_usd) > 0.005 or abs(saldo_ars) > 0.05:
            estados_cuenta.append({
                'id': prov['id'],
                'nombre': prov['razon_social'] or f"{prov['nombre']} {prov['apellido']}",
                'saldo_pendiente_usd': round(saldo_usd, 2),
                'saldo_pendiente_ars': round(saldo_ars, 2)
            })

    return render_template('cuentas_corrientes/listar_proveedores_cc.html', estados_cuenta=estados_cuenta)


# --- NUEVA RUTA: Detalle de Movimientos Cta. Cte. Proveedor ---


#Funcion cuente corriente Cliente#

@app.route('/cuentas_corrientes/clientes')
@login_required
def listar_clientes_cc():
    clientes = db_query("SELECT id, nombre, apellido, razon_social, telefono FROM personas WHERE es_cliente = 1 ORDER BY apellido, nombre, razon_social")
    estados_cuenta = []
    
    for cli in clientes:
        # --- DEUDA USD (Ventas de Equipos) ---
        total_ventas_usd = db_query("""
            SELECT COALESCE(SUM(precio_final_usd), 0) as total 
            FROM ventas WHERE cliente_id = ? AND status = 'COMPLETADA'
        """, (cli['id'],))[0]['total']
        
        pagos_ventas_usd = db_query("""
            SELECT COALESCE(SUM(monto_usd), 0) as total 
            FROM cobros_clientes WHERE cliente_id = ? AND monto_usd > 0
        """, (cli['id'],))[0]['total']
        
        # También restamos lo pagado inicialmente en USD al momento de la venta
        pagos_iniciales_usd = db_query("SELECT COALESCE(SUM(monto_cobrado_usd), 0) FROM ventas WHERE cliente_id = ? AND status = 'COMPLETADA'", (cli['id'],))
        
        saldo_usd = total_ventas_usd - pagos_ventas_usd - pagos_iniciales_usd[0][0]

        # --- DEUDA ARS (Servicios Técnicos / Reparaciones) ---
        total_servicios_ars = db_query("""
            SELECT COALESCE(SUM(precio_final_ars), 0) as total 
            FROM servicios_reparacion WHERE cliente_id = ? AND status = 'COMPLETADO'
        """, (cli['id'],))[0]['total']
        
        pagos_servicios_ars = db_query("""
            SELECT COALESCE(SUM(monto_ars), 0) as total 
            FROM cobros_clientes WHERE cliente_id = ? AND monto_usd = 0
        """, (cli['id'],))[0]['total']

        saldo_ars = total_servicios_ars - pagos_servicios_ars

        if abs(saldo_usd) > 0.01 or abs(saldo_ars) > 0.01:
            estados_cuenta.append({
                'id': cli['id'],
                'nombre': cli['razon_social'] or f"{cli['nombre']} {cli['apellido']}",
                'saldo_usd': saldo_usd,
                'saldo_ars': saldo_ars
            })

    return render_template('cuentas_corrientes/listar_clientes_cc.html', estados_cuenta=estados_cuenta)


##Reporte de cumpleaños
@app.route('/reportes/cumpleanos')
@login_required
#@admin_required
def reporte_cumpleanos():
    # Obtener fechas de filtro o usar hoy por defecto
    hoy_dt = datetime.now()
    start_date_str = request.args.get('start_date', hoy_dt.strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', hoy_dt.strftime('%Y-%m-%d'))

    # Convertir strings a objetos date para extraer mes y día
    start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')

    # Formatear para SQLite (MM-DD)
    m_d_inicio = start_dt.strftime('%m-%d')
    m_d_fin = end_dt.strftime('%m-%d')

    # Consulta: strftime('%m-%d', fecha_nacimiento) extrae mes y día de la DB
    # Manejamos el caso de que el rango cruce de diciembre a enero (ej: 28-dic al 05-ene)
    if m_d_inicio <= m_d_fin:
        query = """
            SELECT nombre, apellido, razon_social, telefono, fecha_nacimiento 
            FROM personas 
            WHERE strftime('%m-%d', fecha_nacimiento) BETWEEN ? AND ?
            ORDER BY strftime('%m-%d', fecha_nacimiento) ASC
        """
        params = (m_d_inicio, m_d_fin)
    else:
        query = """
            SELECT nombre, apellido, razon_social, telefono, fecha_nacimiento 
            FROM personas 
            WHERE strftime('%m-%d', fecha_nacimiento) >= ? 
               OR strftime('%m-%d', fecha_nacimiento) <= ?
            ORDER BY strftime('%m-%d', fecha_nacimiento) ASC
        """
        params = (m_d_inicio, m_d_fin)

    cumpleaneros_raw = db_query(query, params)
    
    cumpleanos = []
    for p in cumpleaneros_raw:
        # Preparar mensaje de WhatsApp
        nombre_cliente = p['nombre'] or p['razon_social']
        mensaje = (
            f"¡Hola {nombre_cliente}! 🎉 Desde el equipo de *My Point* queremos desearte un muy feliz cumpleaños. 🎂\n\n"
            f"Esperamos que pases un día increíble. Queríamos aprovechar este momento para agradecerte de corazón "
            f"la confianza que siempre depositas en nosotros para cuidar tu tecnología. ❤️\n\n"
            f"Nuestro compromiso es seguir brindándote lo mejor. ¡Que se cumplan todos tus deseos! 📱✨"
        )
        
        p_dict = dict(p)
        p_dict['mensaje_wa'] = mensaje
        # Calcular edad (opcional)
        if p['fecha_nacimiento']:
            anio_nac = int(p['fecha_nacimiento'][:4])
            p_dict['edad_cumple'] = hoy_dt.year - anio_nac
        
        cumpleanos.append(p_dict)

    return render_template('reportes/cumpleanos.html', 
                           cumpleanos=cumpleanos, 
                           start_date=start_date_str, 
                           end_date=end_date_str)



@app.route('/reportes/regalos_promociones')
@login_required
@admin_required
def reporte_regalos_promociones():
    start_date, end_date_display, end_date_query = get_date_filters()
    
    # LA CLAVE ESTÁ EN ESTE SELECT: ipv.costo_usd_momento
    query = """
        SELECT 
            v.id AS venta_id, v.fecha_venta, 
            c.marca, c.modelo, c.imei,
            p.nombre, p.apellido, p.razon_social,
            ipv.cantidad,
            r.nombre_parte, r.modelo_compatible, 
            ipv.costo_usd_momento AS costo_unit_regalo  -- <--- ESTO CONGELA EL VALOR
        FROM ventas v
        JOIN celulares c ON v.celular_id = c.id
        JOIN personas p ON v.cliente_id = p.id
        LEFT JOIN items_promocionales_venta ipv ON v.id = ipv.venta_id
        LEFT JOIN repuestos r ON ipv.repuesto_id = r.id
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
        ORDER BY v.fecha_venta DESC, v.id ASC
    """
    results = db_query(query, (start_date, end_date_query))

    ventas_procesadas = {}
    for row in results:
        vid = row['venta_id']
        if vid not in ventas_procesadas:
            ventas_procesadas[vid] = {
                'venta_id': vid,
                'fecha': row['fecha_venta'],
                'cliente': row['razon_social'] or f"{row['nombre']} {row['apellido']}",
                'equipo': f"{row['marca']} {row['modelo']}",
                'imei': row['imei'],
                'regalos': [],
                'costo_total_regalos_usd': 0.0
            }
        
        if row['nombre_parte']:
            # Usamos el costo que guardamos en la tabla intermedia, no el de la tabla repuestos
            c_unit = row['costo_unit_regalo'] if row['costo_unit_regalo'] else 0.0
            c_total = row['cantidad'] * c_unit
            
            ventas_procesadas[vid]['regalos'].append({
                'nombre': row['nombre_parte'],
                'modelo': row['modelo_compatible'],
                'cantidad': row['cantidad'],
                'costo_unit': c_unit
            })
            ventas_procesadas[vid]['costo_total_regalos_usd'] += c_total

    return render_template('reportes/regalos_ventas.html', 
                           ventas=ventas_procesadas.values(),
                           start_date=start_date, 
                           end_date=end_date_display)

##reporte de cuotas pendientes
@app.route('/reportes/cuotas_pendientes')
@login_required
#@admin_required
def reporte_cuotas_pendientes():
    hoy = datetime.now().date()
    
    query = """
        SELECT vc.id as cuota_id, vc.numero_cuota, vc.monto_ars, vc.fecha_vencimiento, 
               v.id as venta_id, v.cantidad_cuotas, v.valor_dolar_momento,
               p.id as cliente_id, p.nombre, p.apellido, p.razon_social, p.telefono
        FROM ventas_cuotas vc
        JOIN ventas v ON vc.venta_id = v.id
        JOIN personas p ON v.cliente_id = p.id
        WHERE vc.estado = 'PENDIENTE'
        ORDER BY vc.fecha_vencimiento ASC
    """
    cuotas_raw = db_query(query)
    
    cuotas_list = []
    total_vencido_usd = 0
    total_proximo_usd = 0

    for c in cuotas_raw:
        f_venc_actual = datetime.strptime(c['fecha_vencimiento'], '%Y-%m-%d').date()
        dias_dif_actual = (f_venc_actual - hoy).days
        
        cotiz = c['valor_dolar_momento'] if c['valor_dolar_momento'] > 0 else 1.0
        monto_usd_actual = c['monto_ars'] / cotiz
        cliente_nombre = c['razon_social'] or f"{c['nombre']} {c['apellido']}"
        
        # Totales para las tarjetas superiores
        if dias_dif_actual < 0:
            total_vencido_usd += monto_usd_actual
        else:
            total_proximo_usd += monto_usd_actual

        # --- CONSTRUCCIÓN DEL MENSAJE DE WHATSAPP CON LÓGICA INTELIGENTE ---
        todas_las_cuotas = db_query("""
            SELECT numero_cuota, monto_ars, estado, fecha_vencimiento 
            FROM ventas_cuotas 
            WHERE venta_id = ? 
            ORDER BY numero_cuota
        """, (c['venta_id'],))
        
        link_pdf = f"{request.host_url.rstrip('/')}{url_for('ver_plan_cuotas', venta_id=c['venta_id'])}"

        msg = f"*ESTADO DE CUENTA - MY POINT*\n"
        msg += f"----------------------------------\n"
        msg += f"*Cliente:* {cliente_nombre}\n"
        msg += f"*Venta:* #{c['venta_id']}\n"
        msg += f"----------------------------------\n"
        
        saldo_pendiente_total = 0
        for cuo in todas_las_cuotas:
            m_u = cuo['monto_ars'] / cotiz
            f_venc_cuo = datetime.strptime(cuo['fecha_vencimiento'], '%Y-%m-%d').date()
            venc_str = f_venc_cuo.strftime('%d/%m/%Y')
            
            # LÓGICA INTELIGENTE: Si el monto es menor a 0.05 USD, se considera PAGADA
            es_pagada = (cuo['estado'] == 'PAGADO' or m_u < 0.05)

            if es_pagada:
                msg += f"✅ Cuota {cuo['numero_cuota']}: u$d 0.00 (Pagada)\n"
            else:
                saldo_pendiente_total += m_u
                dias_atraso = (f_venc_cuo - hoy).days
                
                # Semáforo para el mensaje
                if dias_atraso < 0:
                    msg += f"🔴 Cuota {cuo['numero_cuota']}: u$d {m_u:,.2f} (VENCIDA {venc_str})\n"
                elif dias_atraso <= 7:
                    msg += f"🟡 Cuota {cuo['numero_cuota']}: u$d {m_u:,.2f} (VENCE PRONTO {venc_str})\n"
                else:
                    msg += f"⏳ Cuota {cuo['numero_cuota']}: u$d {m_u:,.2f} (Pendiente {venc_str})\n"
        
        msg += f"----------------------------------\n"
        msg += f"*Total Pendiente: u$d {saldo_pendiente_total:,.2f}*\n\n"
        ##msg += f"📄 *Ver Plan Detallado:* \n{link_pdf}"

        cuotas_list.append({
            'cuota_id': c['cuota_id'],
            'cliente_id': c['cliente_id'],
            'cliente_nombre': cliente_nombre,
            'telefono': c['telefono'],
            'venta_id': c['venta_id'],
            'cuota_nro': f"{c['numero_cuota']}/{c['cantidad_cuotas']}",
            'monto_usd': monto_usd_actual,
            'vencimiento': f_venc_actual,
            'dias_atraso': abs(dias_dif_actual) if dias_dif_actual < 0 else 0,
            'estado_venc': 'VENCIDA' if dias_dif_actual < 0 else ('URGENTE' if dias_dif_actual <= 7 else 'A_VENCER'),
            'mensaje_whatsapp_full': msg 
        })

    return render_template('reportes/cuotas_pendientes.html', 
                           cuotas=cuotas_list, 
                           total_vencido=total_vencido_usd, 
                           total_proximo=total_proximo_usd,
                           hoy=hoy)
    
    
@app.route('/cuentas_corrientes/cobrar_cliente/<int:cliente_id>', methods=['GET', 'POST'])
@login_required
def cobrar_cliente(cliente_id):
    # 1. Obtener los datos del cliente
    cliente_res = db_query("SELECT * FROM personas WHERE id = ?", (cliente_id,))
    if not cliente_res:
        flash("Cliente no encontrado.", "danger")
        return redirect(url_for('listar_clientes_cc'))
    cliente = cliente_res[0]
    
    hoy = datetime.now().date()
    imputacion_elegida = request.form.get('imputacion', 'EQUIPOS') if request.method == 'POST' else request.args.get('imputacion', 'EQUIPOS')

    items_pendientes = []
    
    if imputacion_elegida == 'EQUIPOS':
        # Buscamos CUOTAS: El saldo real es 'monto_ars' (lo que falta pagar)
        items_pendientes = db_query("""
            SELECT 'CUOTA' as tipo, vc.id, vc.venta_id, vc.numero_cuota, 
                   vc.monto_ars, v.valor_dolar_momento,
                   (vc.monto_ars / v.valor_dolar_momento) as saldo_pendiente, 
                   vc.fecha_vencimiento as fecha
            FROM ventas_cuotas vc
            JOIN ventas v ON vc.venta_id = v.id
            WHERE v.cliente_id = ? AND vc.estado = 'PENDIENTE' AND v.status = 'COMPLETADA'
            ORDER BY vc.fecha_vencimiento ASC
        """, (cliente_id,))
    else:
        # Buscar Servicios: El saldo está en ARS
        items_pendientes = db_query("""
            SELECT 'SERVICIO' as tipo, id, NULL as venta_id, NULL as numero_cuota,
                   COALESCE(saldo_pendiente, 0) as saldo_pendiente, fecha_servicio as fecha
            FROM servicios_reparacion 
            WHERE cliente_id = ? AND status = 'COMPLETADO' 
            AND COALESCE(saldo_pendiente, 0) > 0.01 
            ORDER BY fecha_servicio ASC
        """, (cliente_id,))
    
    items_pendientes = [dict(i) for i in items_pendientes]
    for item in items_pendientes:
        try:
            fecha_str = item['fecha'][:10] if item['fecha'] else str(hoy)
            fecha_venc = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except:
            fecha_venc = hoy
        item['vencida'] = fecha_venc < hoy

    total_deuda = sum(i['saldo_pendiente'] for i in items_pendientes)
    dolar_info = obtener_cotizacion_dolar()

    if request.method == 'POST':
        db_conn = get_db()
        try:
            # --- SELECCIÓN DE DÓLAR PARA LA ENTRADA DE DINERO ---
            tipo_dolar_elegido = request.form.get('tipo_dolar', 'blue')
            if tipo_dolar_elegido == 'oficial':
                cotiz_hoy = float(dolar_info['compra'] or 1.0)
            elif tipo_dolar_elegido == 'manual':
                cotiz_hoy = float(request.form.get('valor_dolar_manual') or 1.0)
            else:
                cotiz_hoy = float(dolar_info['compra_blue'] or 1.0)

            monto_fisico_entregado = float(request.form.get('monto_a_cobrar', 0) or 0)
            moneda_pago = request.form.get('moneda', 'ARS')
            cuenta_destino = request.form.get('cuenta_destino', 'EFECTIVO')
            observaciones = request.form.get('observaciones', '')

            # --- CAPTURA DE ASIGNACIÓN ---
            ids_comprobantes = request.form.getlist('item_id[]')
            tipos_comprobantes = request.form.getlist('item_tipo[]')
            montos_aplicados = request.form.getlist('monto_aplicado[]')

            if monto_fisico_entregado <= 0:
                flash("El monto a cobrar debe ser positivo.", "danger")
                return redirect(url_for('cobrar_cliente', cliente_id=cliente_id, imputacion=imputacion_elegida))
            
            # Valor contable para el Haber
            monto_ingresado_en_usd = monto_fisico_entregado if moneda_pago == 'USD' else (monto_fisico_entregado / cotiz_hoy)
            monto_ars_contable = monto_fisico_entregado if moneda_pago == 'ARS' else (monto_fisico_entregado * cotiz_hoy)
            monto_usd_fisico = monto_fisico_entregado if moneda_pago == 'USD' else 0.0

            db_conn.execute("BEGIN TRANSACTION")
            
            items_pagados_detalle = []
            total_impacto_usd_aplicado = 0

            for i in range(len(ids_comprobantes)):
                monto_especifico_input = float(montos_aplicados[i] or 0)
                
                if monto_especifico_input > 0:
                    id_ref = ids_comprobantes[i]
                    tipo_ref = tipos_comprobantes[i]
                    
                    if tipo_ref == 'CUOTA':
                        # El monto_especifico_input viene en USD
                        total_impacto_usd_aplicado += monto_especifico_input
                        
                        # Obtenemos datos de la venta original para descontar PESOS correctamente del saldo
                        venta = db_query_func(db_conn, "SELECT v.valor_dolar_momento, v.id as v_id FROM ventas_cuotas vc JOIN ventas v ON vc.venta_id = v.id WHERE vc.id = ?", (id_ref,))[0]
                        monto_ars_a_restar = monto_especifico_input * venta['valor_dolar_momento']
                        
                        # ACTUALIZACIÓN DEL SALDO PENDIENTE (Semaforo)
                        # Restamos de monto_ars (el saldo), pero NUNCA tocamos monto_original_ars
                        db_execute_func(db_conn, "UPDATE ventas_cuotas SET monto_ars = monto_ars - ? WHERE id = ?", (monto_ars_a_restar, id_ref))
                        
                        # Si el saldo es casi cero, marcar como PAGADO para que salga del semáforo
                        db_execute_func(db_conn, "UPDATE ventas_cuotas SET estado = 'PAGADO' WHERE id = ? AND monto_ars <= 1.0", (id_ref,))
                        
                        # Actualizar saldo global de la venta
                        db_execute_func(db_conn, "UPDATE ventas SET saldo_pendiente = saldo_pendiente - ? WHERE id = ?", (monto_ars_a_restar, venta['v_id']))
                        
                        items_pagados_detalle.append(f"Cuota ID:{id_ref} (u$d {monto_especifico_input})")
                    
                    else:
                        # Servicios (ARS)
                        impacto_en_usd = monto_especifico_input / cotiz_hoy
                        total_impacto_usd_aplicado += impacto_en_usd
                        
                        db_execute_func(db_conn, "UPDATE servicios_reparacion SET saldo_pendiente = saldo_pendiente - ? WHERE id = ?", (monto_especifico_input, id_ref))
                        items_pagados_detalle.append(f"Servicio #{id_ref} (-${monto_especifico_input})")

            # Registro de cobro (Este es el HABER que cancelará el DEBE original en el historial)
            referencia = ", ".join(items_pagados_detalle)
            cobro_id = db_execute_func(db_conn, """
                INSERT INTO cobros_clientes (cliente_id, user_id, fecha_cobro, monto_ars, monto_usd, metodo_pago, referencia, observaciones, imputacion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (cliente_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                  monto_ars_contable, monto_usd_fisico, cuenta_destino, referencia, observaciones, imputacion_elegida), return_id=True)

            # Impacto en Caja
            if moneda_pago == 'USD':
                registrar_movimiento_caja(current_user.id, 'INGRESO_COBRO_DEUDA_USD', 0, monto_usd_fisico, 
                                          f"Cobro {imputacion_elegida} u$d - {cliente['nombre']}", cobro_id, None, cuenta_destino)
            else:
                registrar_movimiento_caja(current_user.id, 'INGRESO_COBRO_DEUDA_ARS', monto_ars_contable, 0, 
                                          f"Cobro {imputacion_elegida} - {cliente['nombre']}", cobro_id, None, cuenta_destino)

            db_conn.commit()
            flash(f"Cobro registrado y saldos actualizados correctamente.", "success")
            return redirect(url_for('listar_clientes_cc'))

        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error cobro: {e}", exc_info=True)
            flash(f"Error: {e}", "danger")
            return redirect(url_for('cobrar_cliente', cliente_id=cliente_id))

    return render_template('cuentas_corrientes/cobrar_cliente.html', 
                           cliente=cliente, items=items_pendientes, 
                           total_deuda=total_deuda, dolar_info=dolar_info,
                           imputacion=imputacion_elegida, hoy=hoy)
    
    
    
@app.route('/cuentas_corrientes/registrar_pago/<int:proveedor_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def registrar_pago_proveedor(proveedor_id):
    proveedor = db_query("SELECT id, nombre, apellido, razon_social FROM personas WHERE id = ? AND es_proveedor = 1", (proveedor_id,))
    if not proveedor:
        flash("Proveedor no encontrado.", "danger")
        return redirect(url_for('listar_proveedores_cc'))
    proveedor = proveedor[0]
    
    dolar_info = inject_dolar_values()
    valor_dolar_compra = dolar_info['valor_dolar_compra'] or 1.0

    # DETERMINAR QUÉ BOLSA DE DEUDA ESTAMOS PAGANDO
    imputacion_elegida = request.form.get('imputacion', 'EQUIPOS') if request.method == 'POST' else request.args.get('imputacion', 'EQUIPOS')

    # Filtro estricto para hardware que se rige por USD
    lista_equipos_tipos = "('CELULAR', 'TABLET', 'SMARTWATCH', 'EQUIPO')"
    
    if imputacion_elegida == 'EQUIPOS':
        filtro_tipo = f"AND c.tipo_item IN {lista_equipos_tipos}"
    else:
        filtro_tipo = f"AND c.tipo_item NOT IN {lista_equipos_tipos}"

    # 1. Obtener Deuda Detallada (CORREGIDO: Subconsultas filtradas por imputación para evitar duplicados)
    compras_raw = db_query(f"""
        SELECT c.id, c.fecha_compra, c.costo_total_ars, c.costo_total_usd, c.tipo_item, c.estado_pago,
               CASE
                   WHEN c.tipo_item IN {lista_equipos_tipos} THEN COALESCE(cel.marca, 'Equipo') || ' ' || COALESCE(cel.modelo, '') || ' (SN: ' || COALESCE(cel.imei, 'N/A') || ')'
                   ELSE rep.nombre_parte || ' (' || COALESCE(rep.modelo_compatible, 'Genérico') || ')'
               END AS item_descripcion,
               (SELECT COALESCE(SUM(p.monto_ars), 0) FROM pagos_proveedores p WHERE p.compra_id = c.id AND p.imputacion = 'REPUESTOS') as pagado_ars_total_acumulado,
               (SELECT COALESCE(SUM(p.monto_usd), 0) FROM pagos_proveedores p WHERE p.compra_id = c.id AND p.imputacion = 'EQUIPOS') as pagado_usd_impacto_acumulado
        FROM compras c
        LEFT JOIN celulares cel ON c.item_id = cel.id AND c.tipo_item IN {lista_equipos_tipos}
        LEFT JOIN repuestos rep ON c.item_id = rep.id AND c.tipo_item NOT IN {lista_equipos_tipos}
        WHERE c.proveedor_id = ? AND c.estado_pago != 'PAGADO_TOTAL' {filtro_tipo}
        ORDER BY c.fecha_compra ASC
    """, (proveedor_id,))

    compras_pendientes = []
    total_deuda_ars = 0.0
    total_deuda_usd = 0.0

    for c in compras_raw:
        deuda_restante_usd = c['costo_total_usd'] - c['pagado_usd_impacto_acumulado']
        deuda_restante_ars = c['costo_total_ars'] - c['pagado_ars_total_acumulado']
        
        deuda_activa = deuda_restante_usd if imputacion_elegida == 'EQUIPOS' else deuda_restante_ars
        
        if deuda_activa > 0.005: 
            compras_pendientes.append({
                'compra_id': c['id'],
                'fecha_compra': c['fecha_compra'],
                'item_descripcion': c['item_descripcion'],
                'deuda_restante_ars': deuda_restante_ars,
                'deuda_restante_usd': deuda_restante_usd
            })
            total_deuda_ars += deuda_restante_ars
            total_deuda_usd += deuda_restante_usd

    if request.method == 'POST':
        db_conn = get_db()
        try:
            monto_fisico_ars = float(request.form.get('monto_ars', 0) or 0)
            monto_fisico_usd = float(request.form.get('monto_usd', 0) or 0)
            entidad_pago_ars = request.form.get('cuenta_pago_ars', 'EFECTIVO')
            entidad_origen_usd = request.form.get('entidad_origen_usd', 'EFECTIVO')
            
            tipo_dolar_pago = request.form.get('tipo_dolar', 'blue')
            cotiz_pago = float(request.form.get('valor_dolar_manual') or valor_dolar_compra) if tipo_dolar_pago == 'manual' else valor_dolar_compra

            db_conn.execute("BEGIN TRANSACTION")

            for compra in compras_pendientes:
                cid = str(compra['compra_id'])
                asignado_ars = float(request.form.get(f'monto_a_pagar_ars_{cid}', 0) or 0)
                asignado_usd = float(request.form.get(f'monto_a_pagar_usd_{cid}', 0) or 0)
                
                if asignado_ars > 0 or asignado_usd > 0:
                     if imputacion_elegida == 'EQUIPOS':
                         # --- IMPACTO USD LIMPIO PARA EQUIPOS ---
                         # Convertimos todo lo asignado a la deuda (Pesos y Dólares) a un único valor USD
                         impacto_usd_total_este_pago = asignado_usd + (asignado_ars / cotiz_pago)
                         
                         # GUARDADO: Forzamos monto_ars a 0 para que no se sume dos veces en reportes mixtos
                         db_execute_func(db_conn, """
                            INSERT INTO pagos_proveedores (proveedor_id, user_id, fecha_pago, compra_id, monto_ars, monto_usd, valor_dolar_momento, tipo_pago, imputacion)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (proveedor_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                              compra['compra_id'], 0, impacto_usd_total_este_pago, cotiz_pago, entidad_pago_ars, 'EQUIPOS'))
                         
                         estado_nuevo = 'PAGADO_TOTAL' if impacto_usd_total_este_pago >= (compra['deuda_restante_usd'] - 0.01) else 'PAGADO_PARCIAL'
                     
                     else:
                         # --- IMPACTO ARS LIMPIO PARA REPUESTOS ---
                         # Convertimos todo lo asignado a un único valor ARS
                         impacto_ars_total_este_pago = asignado_ars + (asignado_usd * cotiz_pago)
                         
                         # GUARDADO: Forzamos monto_usd a 0 para que no ensucie saldos en dólares
                         db_execute_func(db_conn, """
                            INSERT INTO pagos_proveedores (proveedor_id, user_id, fecha_pago, compra_id, monto_ars, monto_usd, valor_dolar_momento, tipo_pago, imputacion)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (proveedor_id, current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                              compra['compra_id'], impacto_ars_total_este_pago, 0, cotiz_pago, entidad_pago_ars, 'REPUESTOS'))
                         
                         estado_nuevo = 'PAGADO_TOTAL' if impacto_ars_total_este_pago >= (compra['deuda_restante_ars'] - 0.05) else 'PAGADO_PARCIAL'
                     
                     db_execute_func(db_conn, "UPDATE compras SET estado_pago = ? WHERE id = ?", (estado_nuevo, compra['compra_id']))

            # 2. IMPACTO EN CAJA FÍSICA (Independiente de la Cta Cte)
            if monto_fisico_ars > 0:
                registrar_movimiento_caja(current_user.id, 'EGRESO_PAGO_PROVEEDOR_ARS', monto_ars=monto_fisico_ars, 
                                          descripcion=f"Pago {imputacion_elegida} a {proveedor['razon_social']}", 
                                          metodo_pago=entidad_pago_ars)

            if monto_fisico_usd > 0:
                registrar_movimiento_caja(current_user.id, 'EGRESO_PAGO_PROVEEDOR_USD', monto_usd=monto_fisico_usd, 
                                          descripcion=f"Pago {imputacion_elegida} USD a {proveedor['razon_social']}", 
                                          metodo_pago=entidad_origen_usd)

            db_conn.commit()
            flash(f"Pago de {imputacion_elegida} registrado con éxito.", "success")
            return redirect(url_for('listar_proveedores_cc'))

        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error pago proveedor: {e}", exc_info=True)
            flash(f"Error: {e}", "danger")

    return render_template('cuentas_corrientes/registrar_pago.html', 
                           proveedor=proveedor, compras_pendientes=compras_pendientes, 
                           total_deuda_ars=total_deuda_ars, total_deuda_usd=total_deuda_usd,
                           valor_dolar_compra=valor_dolar_compra, form_data=request.form,
                           imputacion=imputacion_elegida)
    
      
      
# --- NUEVA RUTA: Detalle de Movimientos Cta. Cte. Cliente ---
# --- RUTA ACTUALIZADA: Detalle de Movimientos Cta. Cte. Cliente ---
# --- RUTA ACTUALIZADA: Detalle de Movimientos Cta. Cte. Cliente ---

@app.route('/cuentas_corrientes/cliente/detalle/<int:cliente_id>')
@login_required
def ver_detalle_cc_cliente(cliente_id):
    cliente = db_query("SELECT * FROM personas WHERE id = ?", (cliente_id,))[0]
    movimientos = []
    hoy = datetime.now().date() 
    
    dolar_info = obtener_cotizacion_dolar()
    cotiz_actual = float(dolar_info['compra_blue'] or 1.0)

    # 1. DEBE: Ventas y sus CUOTAS (Moneda base: USD)
    ventas = db_query("""
        SELECT id, fecha_venta, precio_final_ars, precio_final_usd, valor_dolar_momento, 
               cantidad_cuotas, monto_cobrado_ars, monto_cobrado_usd, 
               monto_transferencia_ars, monto_mp_ars, monto_debito_ars, 
               monto_credito_ars, monto_virtual_usd, valor_celular_parte_pago
        FROM ventas WHERE cliente_id = ? AND status = 'COMPLETADA'
    """, (cliente_id,))
    
    for v in ventas:
        cotiz_v = v['valor_dolar_momento'] or 1.0
        
        # --- A. Entrega Inicial ---
        total_pago_inicial_ars = (v['monto_cobrado_ars'] or 0) + (v['monto_transferencia_ars'] or 0) + \
                                 (v['monto_mp_ars'] or 0) + (v['monto_debito_ars'] or 0) + \
                                 (v['monto_credito_ars'] or 0) + (v['valor_celular_parte_pago'] or 0)
        
        total_pago_inicial_usd = (v['monto_cobrado_usd'] or 0) + (v['monto_virtual_usd'] or 0) + (total_pago_inicial_ars / cotiz_v)

        if total_pago_inicial_usd > 0.01:
            movimientos.append({
                'fecha': v['fecha_venta'],
                'tipo': 'DEBE',
                'monto_ars': total_pago_inicial_usd * cotiz_v,
                'monto_reg': total_pago_inicial_usd,
                'moneda_display': 'USD',
                'descripcion': f"Venta #{v['id']} - Entrega Inicial Acordada", 
                'rubro': 'EQUIPOS',
                'cotizacion': cotiz_v,
                'vencida': False,
                'es_cuota': False,
                'ref': v['id']
            })

        # --- B. CUOTAS EN EL DEBE (AQUÍ ESTÁ LA CORRECCIÓN) ---
        if v['cantidad_cuotas'] >= 1:
            cuotas = db_query("SELECT * FROM ventas_cuotas WHERE venta_id = ?", (v['id'],))
            for c in cuotas:
                try:
                    fecha_venc = datetime.strptime(c['fecha_vencimiento'][:10], '%Y-%m-%d').date()
                except:
                    fecha_venc = hoy
                es_vencida = fecha_venc < hoy and c['estado'] == 'PENDIENTE'
                
                # CLAVE: Si existe monto_original_ars lo usamos, sino usamos monto_ars (para ventas viejas)
                # Esto garantiza que el DEBE no se mueva aunque se cobre la cuota.
                monto_deuda_original = c['monto_original_ars'] if (c['monto_original_ars'] and c['monto_original_ars'] > 0) else c['monto_ars']

                movimientos.append({
                    'fecha': c['fecha_vencimiento'] + " 09:00:00",
                    'tipo': 'DEBE', 
                    'monto_ars': monto_deuda_original,
                    'monto_reg': monto_deuda_original / cotiz_v, # El debe siempre usa el valor original
                    'moneda_display': 'USD',
                    'descripcion': f"Venta #{v['id']} - Cuota {c['numero_cuota']}/{v['cantidad_cuotas']}", 
                    'rubro': 'EQUIPOS',
                    'cotizacion': cotiz_v,
                    'vencida': es_vencida,
                    'es_cuota': True,
                    'ref': v['id']
                })

    # 2. DEBE: Servicios (Inamovible porque precio_final_ars no se toca)
    servicios = db_query("SELECT id, fecha_servicio as fecha, precio_final_ars as monto, falla_reportada as descripcion FROM servicios_reparacion WHERE cliente_id = ? AND status = 'COMPLETADO'", (cliente_id,))
    for s in servicios:
        movimientos.append({
            'fecha': s['fecha'], 'tipo': 'DEBE', 'monto_ars': s['monto'], 'monto_reg': s['monto'], 
            'moneda_display': 'ARS', 'descripcion': f"Servicio #{s['id']} - {s['descripcion']}", 
            'rubro': 'SERVICIOS', 'cotizacion': None, 'vencida': False, 'es_cuota': False, 'ref': s['id']
        })

    # 3. HABER: Cobros (Aquí es donde se descuenta el saldo realmente)
    cobros = db_query("SELECT id, fecha_cobro as fecha, monto_ars, monto_usd, metodo_pago, observaciones, imputacion FROM cobros_clientes WHERE cliente_id = ?", (cliente_id,))
    for p in cobros:
        if p['imputacion'] == 'EQUIPOS':
            # Calculamos el impacto en USD del cobro realizado
            monto_reg = p['monto_usd'] if p['monto_usd'] > 0 else (p['monto_ars'] / cotiz_actual)
            movimientos.append({
                'fecha': p['fecha'], 'tipo': 'HABER', 'monto_ars': p['monto_ars'], 'monto_reg': monto_reg, 
                'moneda_display': 'USD', 'descripcion': f"Pago Cta.Cte. ({p['metodo_pago']})", 
                'rubro': 'EQUIPOS', 'cotizacion': cotiz_actual, 'es_cuota': False, 'ref': p['id']
            })
        else:
            movimientos.append({
                'fecha': p['fecha'], 'tipo': 'HABER', 'monto_ars': p['monto_ars'], 'monto_reg': p['monto_ars'], 
                'moneda_display': 'ARS', 'descripcion': f"Pago Cta.Cte. ({p['metodo_pago']})", 
                'rubro': 'SERVICIOS', 'cotizacion': None, 'es_cuota': False, 'ref': p['id']
            })

    # 4. HABER: Pagos Iniciales de Ventas
    for v in ventas:
        cotiz_h = v['valor_dolar_momento'] or 1.0
        total_ars_h = (v['monto_cobrado_ars'] or 0) + (v['monto_transferencia_ars'] or 0) + \
                      (v['monto_mp_ars'] or 0) + (v['monto_debito_ars'] or 0) + \
                      (v['monto_credito_ars'] or 0) + (v['valor_celular_parte_pago'] or 0)
        
        monto_reg_usd = (v['monto_cobrado_usd'] or 0) + (v['monto_virtual_usd'] or 0) + (total_ars_h / cotiz_h)
        
        if monto_reg_usd > 0.01:
            movimientos.append({
                'fecha': v['fecha_venta'], 'tipo': 'HABER', 'monto_ars': monto_reg_usd * cotiz_h, 
                'monto_reg': monto_reg_usd, 'moneda_display': 'USD', 
                'descripcion': f"Pago Inicial Recibido - Venta #{v['id']}", 
                'rubro': 'EQUIPOS', 'cotizacion': cotiz_h, 'es_cuota': False, 'ref': v['id']
            })

    # Ordenar y calcular saldo acumulado
    movimientos.sort(key=lambda x: x['fecha'])
    saldo_equipos_usd_acum = 0
    saldo_servicios_ars_acum = 0

    for mov in movimientos:
        imp_reg = mov['monto_reg'] if mov['tipo'] == 'DEBE' else -mov['monto_reg']
        if mov['rubro'] == 'EQUIPOS':
            saldo_equipos_usd_acum += imp_reg
            mov['saldo_rubro_acum'] = saldo_equipos_usd_acum
        else:
            saldo_servicios_ars_acum += imp_reg
            mov['saldo_rubro_acum'] = saldo_servicios_ars_acum

    return render_template('cuentas_corrientes/detalle_cliente.html', 
                           cliente=cliente, movimientos=movimientos, 
                           saldo_equipos=saldo_equipos_usd_acum,
                           saldo_servicios=saldo_servicios_ars_acum)
    
    
    
@app.route('/ventas/plan_cuotas/<int:venta_id>')
@login_required
def ver_plan_cuotas(venta_id):
    # 1. Obtenemos los datos de la venta y el cliente
    venta_data = db_query("""
        SELECT v.*, p.nombre, p.apellido, p.razon_social, p.cuit_cuil, p.email, p.telefono, p.id as cliente_id
        FROM ventas v 
        JOIN personas p ON v.cliente_id = p.id 
        WHERE v.id = ?
    """, (venta_id,))
    
    if not venta_data:
        flash("Venta no encontrada.", "danger")
        return redirect(url_for('historial_ventas'))
    
    venta = venta_data[0]
    
    # 2. Obtenemos las cuotas calculando el valor en USD en la misma consulta
    # Dividimos el monto en pesos por el valor del dólar que se usó al momento de la venta
    cotizacion_venta = venta['valor_dolar_momento'] if venta['valor_dolar_momento'] and venta['valor_dolar_momento'] > 0 else 1.0
    
    cuotas = db_query("""
        SELECT *, 
               (monto_ars / ?) as monto_usd 
        FROM ventas_cuotas 
        WHERE venta_id = ? 
        ORDER BY numero_cuota ASC
    """, (cotizacion_venta, venta_id))
    
    return render_template('ventas/plan_cuotas.html', venta=venta, cuotas=cuotas)


##Gestion de cuotas de cta cte Cliente
@app.route('/ventas/pagar_cuota/<int:cuota_id>', methods=['POST'])
@login_required
def pagar_cuota(cuota_id):
    db_conn = get_db()
    try:
        cuota = db_query("SELECT * FROM ventas_cuotas WHERE id = ?", (cuota_id,))[0]
        venta = db_query("SELECT * FROM ventas WHERE id = ?", (cuota['venta_id'],))[0]
        
        # Al pagar una cuota, registramos un cobro normal en la cuenta corriente
        # Esto disparará la lógica FIFO que ya tienes o podemos hacerlo manual:
        db_conn.execute("BEGIN TRANSACTION")
        
        # 1. Marcar cuota como pagada
        db_execute_func(db_conn, "UPDATE ventas_cuotas SET estado = 'PAGADO' WHERE id = ?", (cuota_id,))
        
        # 2. Registrar el ingreso en cobros_clientes
        cobro_id = db_execute_func(db_conn, """
            INSERT INTO cobros_clientes (cliente_id, user_id, fecha_cobro, monto_ars, metodo_pago, referencia, imputacion)
            VALUES (?, ?, ?, ?, ?, ?, 'EQUIPOS')
        """, (venta['cliente_id'], current_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
              cuota['monto_ars'], 'EFECTIVO', f"Pago Cuota {cuota['numero_cuota']} Venta #{venta['id']}", 'EQUIPOS'), return_id=True)
        
        # 3. Registrar en Caja
        registrar_movimiento_caja(current_user.id, 'INGRESO_COBRO_CUOTA', cuota['monto_ars'], 0, f"Cuota {cuota['numero_cuota']} Venta {venta['id']}", cobro_id)

        db_conn.commit()
        flash(f"Cuota {cuota['numero_cuota']} pagada correctamente.", "success")
    except Exception as e:
        db_conn.rollback()
        flash(f"Error: {e}", "danger")
    
    return redirect(url_for('ver_plan_cuotas', venta_id=venta['id']))

    
# --- RUTA ACTUALIZADA: Detalle de Movimientos Cta. Cte. Proveedor ---

@app.route('/cuentas_corrientes/proveedor/detalle/<int:proveedor_id>')
@login_required
def ver_detalle_cc_proveedor(proveedor_id):
    # 1. Obtener datos del proveedor
    proveedor_data = db_query("SELECT * FROM personas WHERE id = ?", (proveedor_id,))
    if not proveedor_data:
        flash("Proveedor no encontrado.", "danger")
        return redirect(url_for('listar_proveedores_cc'))
    proveedor = proveedor_data[0]
    
    # 2. Capturamos la vista: 'EQUIPOS' (USD) o 'REPUESTOS' (ARS)
    view = request.args.get('view', 'EQUIPOS')
    
    movimientos = []
    dolar_info = inject_dolar_values()
    # Usamos el dólar de compra actual como fallback si no hay cotización histórica
    valor_dolar_actual = dolar_info['valor_dolar_compra'] or 1.0

    # REGLA ESTRICTA: Tipos que se rigen por DÓLARES (USD)
    tipos_equipos = "('CELULAR', 'TABLET', 'SMARTWATCH', 'EQUIPO')"

    # 3. Procesar Compras (DEBE)
    if view == 'EQUIPOS':
        # Compras de Celulares y Equipos: El saldo se rige por costo_total_usd
        query_compras = f"SELECT * FROM compras WHERE proveedor_id = ? AND tipo_item IN {tipos_equipos}"
    else:
        # Repuestos y Accesorios: El saldo se rige por costo_total_ars
        query_compras = f"SELECT * FROM compras WHERE proveedor_id = ? AND tipo_item NOT IN {tipos_equipos}"
    
    compras = db_query(query_compras, (proveedor_id,))
    for c in compras:
        movimientos.append({
            'fecha': c['fecha_compra'], 
            'tipo': 'DEBE', 
            'monto_usd': c['costo_total_usd'],      # Deuda real en USD
            'monto_ars': c['costo_total_ars'],      # Valor en Pesos
            'monto_ars_info': c['costo_total_ars'],  # Referencia informativa
            'cotizacion': c['valor_dolar_momento'],
            'descripcion': f"Compra {c['tipo_item'].title()} #{c['id']}", 
            'tipo_item': c['tipo_item'],
            'ref': c['id']
        })

    # 4. Procesar Pagos (HABER)
    # Filtramos por la imputación correspondiente ('EQUIPOS' o 'REPUESTOS')
    pagos = db_query("SELECT * FROM pagos_proveedores WHERE proveedor_id = ? AND imputacion = ?", (proveedor_id, view))
    for p in pagos:
        metodo_label = p['tipo_pago'].replace('_', ' ').title() if p['tipo_pago'] else 'General'
        
        # --- SOLUCIÓN A LA DUPLICACIÓN ---
        # Como las funciones de registro ahora guardan el impacto LIMPIO en la columna moneda base:
        if view == 'EQUIPOS':
            # Si estamos viendo equipos, el impacto que descuenta saldo es directamente monto_usd
            impacto_haber = p['monto_usd']
        else:
            # Si estamos viendo repuestos, el impacto que descuenta saldo es directamente monto_ars
            impacto_haber = p['monto_ars']

        movimientos.append({
            'fecha': p['fecha_pago'], 
            'tipo': 'HABER', 
            'monto_usd': p['monto_usd'], # Para mostrar en la tabla (si existiera valor)
            'monto_ars': p['monto_ars'], # Para mostrar en la tabla (si existiera valor)
            'monto_ars_info': p['monto_ars'], 
            'cotizacion': p['valor_dolar_momento'], 
            'impacto_haber': impacto_haber, # Este es el valor que resta del acumulado
            'descripcion': f"Pago realizado ({metodo_label})",
            'ref': p['id']
        })

    # 5. Ordenar cronológicamente y calcular saldo acumulado dinámico
    movimientos.sort(key=lambda x: x['fecha'])
    saldo_acumulado = 0

    for mov in movimientos:
        if mov['tipo'] == 'DEBE':
            # Si la vista es EQUIPOS, sumamos Dólares. Si es REPUESTOS, sumamos Pesos.
            valor_entrada = mov['monto_usd'] if view == 'EQUIPOS' else mov['monto_ars']
            saldo_acumulado += valor_entrada
        else:
            # El Haber (pago) resta según el impacto calculado (Dólares o Pesos)
            saldo_acumulado -= mov['impacto_haber']
        
        mov['saldo_acumulado'] = saldo_acumulado

    return render_template('cuentas_corrientes/detalle_proveedor.html', 
                           proveedor=proveedor, 
                           movimientos=movimientos, 
                           saldo_final=saldo_acumulado,
                           categoria_view=view)
    
    
           
    
@app.route('/ventas/cotizar/<int:celular_id>', methods=['GET', 'POST'])
@login_required
def cotizar_venta(celular_id):
    # Obtenemos las cotizaciones actuales de ambas APIs
    dolar_info = obtener_cotizacion_dolar()
    
    celular = db_query("SELECT * FROM celulares WHERE id = ? AND stock = 1", (celular_id,))
    if not celular:
        flash("Celular no encontrado o no disponible en stock.", "danger")
        return redirect(url_for('inventario_celulares'))
    celular = celular[0]

    if request.method == 'POST':
        db_conn = get_db()
        try:
            db_conn.execute("BEGIN TRANSACTION")

            # --- CAPTURA DE OBSERVACIONES ---
            observaciones_venta = request.form.get('observaciones', '').strip()

            # --- LÓGICA DE SELECCIÓN DE DÓLAR ---
            tipo_dolar_elegido = request.form.get('tipo_dolar', 'blue')
            if tipo_dolar_elegido == 'oficial':
                valor_dolar_venta_local = float(dolar_info['venta'] or 1.0)
            elif tipo_dolar_elegido == 'manual':
                valor_dolar_venta_local = float(request.form.get('valor_dolar_manual') or 1.0)
            else: # blue por defecto
                valor_dolar_venta_local = float(dolar_info['venta_blue'] or 1.0)

            if valor_dolar_venta_local <= 0:
                raise ValueError("La cotización del dólar debe ser mayor a 0.")

            cliente_id = int(request.form['cliente_id'])
            impuestos_pct = float(request.form.get('impuestos_pct', 0) or 0) 

            ganancia_tipo = request.form.get('ganancia_tipo') 
            ganancia_pct = float(request.form.get('ganancia_pct', 0) or 0) if ganancia_tipo == 'porcentaje' else None
            monto_agregado = float(request.form.get('monto_agregado', 0) or 0) if ganancia_tipo == 'monto_fijo' else None
            monto_agregado_moneda = request.form.get('monto_agregado_moneda') if ganancia_tipo == 'monto_fijo' else None
            
            if not cliente_id:
                flash("Debe seleccionar un cliente.", "danger")
                return redirect(url_for('cotizar_venta', celular_id=celular_id))
            
            # --- CÁLCULO BASE DEL CELULAR EN USD ---
            costo_base_usd = celular['costo_usd']
            monto_agregado_ars_db = None 
            monto_agregado_usd_db = None 

            if ganancia_tipo == 'porcentaje':
                precio_final_usd_pre_tax = costo_base_usd * (1 + ganancia_pct / 100)
            else: # monto_fijo
                if monto_agregado_moneda == 'USD':
                    precio_final_usd_pre_tax = costo_base_usd + monto_agregado
                    monto_agregado_usd_db = monto_agregado
                else: # ARS
                    ganancia_en_usd = monto_agregado / valor_dolar_venta_local
                    precio_final_usd_pre_tax = costo_base_usd + ganancia_en_usd
                    monto_agregado_ars_db = monto_agregado

            # ==========================================================
            # === NUEVO: PROCESAR ÍTEMS ADICIONALES (A LA VENTA) ===
            # ==========================================================
            add_ids = request.form.getlist('add_item_id[]')
            add_cants = request.form.getlist('add_cantidad[]')
            add_precios = request.form.getlist('add_precio_usd[]')

            total_adicionales_usd = 0.0
            adicionales_para_db = []

            for i in range(len(add_ids)):
                r_id_str = add_ids[i].strip()
                if r_id_str:
                    r_id = int(r_id_str)
                    cant = int(add_cants[i] or 1)
                    precio_v_usd = float(add_precios[i] or 0)
                    
                    # Buscamos el costo actual para congelarlo (necesario para rentabilidad)
                    rep_data = db_query_func(db_conn, "SELECT costo_usd FROM repuestos WHERE id = ?", (r_id,))
                    costo_momento = rep_data[0]['costo_usd'] if rep_data else 0.0
                    
                    total_adicionales_usd += (precio_v_usd * cant)
                    adicionales_para_db.append({
                        'repuesto_id': r_id,
                        'cantidad': cant,
                        'precio_vendido_usd': precio_v_usd,
                        'costo_usd_momento': costo_momento
                    })

            # Sumamos los adicionales al precio base antes de impuestos
            precio_final_usd_pre_tax += total_adicionales_usd
            # ==========================================================

            # Convertimos el total acumulado de USD a ARS usando el DOLAR ELEGIDO
            precio_final_ars = precio_final_usd_pre_tax * valor_dolar_venta_local

            # Aplicar impuestos sobre el valor resultante en pesos
            if impuestos_pct > 0:
                precio_final_ars *= (1 + impuestos_pct / 100)
            
            # Recalculamos el precio_final_usd para guardar en DB el valor real post-impuestos
            precio_final_usd = precio_final_ars / valor_dolar_venta_local

            fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Guardamos la venta
            venta_id = db_execute_func(db_conn, """
                INSERT INTO ventas (celular_id, cliente_id, fecha_venta, cantidad, valor_dolar_momento, 
                                    impuestos_pct, ganancia_pct, monto_agregado_ars, monto_agregado_usd, 
                                    precio_final_ars, precio_final_usd, status, observaciones) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PRESUPUESTO', ?)""",
                        (celular_id, cliente_id, fecha_actual, 1, valor_dolar_venta_local, 
                         impuestos_pct, ganancia_pct, 
                         monto_agregado_ars_db, monto_agregado_usd_db, 
                         precio_final_ars, precio_final_usd, observaciones_venta), return_id=True)
            
            # --- GUARDAR ÍTEMS ADICIONALES (VENTA PAGADA) ---
            for item in adicionales_para_db:
                db_execute_func(db_conn, """
                    INSERT INTO items_adicionales_venta 
                    (venta_id, repuesto_id, cantidad, precio_vendido_usd, costo_usd_momento) 
                    VALUES (?, ?, ?, ?, ?)
                """, (venta_id, item['repuesto_id'], item['cantidad'], item['precio_vendido_usd'], item['costo_usd_momento']))

            # --- GUARDAR REGALOS CON COSTO CONGELADO ---
            promo_ids = request.form.getlist('promo_item_id[]')
            promo_cants = request.form.getlist('promo_cantidad[]')

            for i in range(len(promo_ids)):
                r_id_promo = promo_ids[i].strip() if promo_ids[i] else None
                if r_id_promo:
                    rep_data = db_query_func(db_conn, "SELECT costo_usd FROM repuestos WHERE id = ?", (r_id_promo,))
                    costo_a_guardar = rep_data[0]['costo_usd'] if rep_data else 0.0
                    
                    db_execute_func(db_conn, 
                        "INSERT INTO items_promocionales_venta (venta_id, repuesto_id, cantidad, costo_usd_momento) VALUES (?, ?, ?, ?)",
                        (venta_id, r_id_promo, promo_cants[i], costo_a_guardar))           
            
            registrar_movimiento(current_user.id, 'CREACION_PRESUPUESTO', 'VENTA', venta_id, {
                'celular_id': celular_id, 
                'cotizacion_aplicada': valor_dolar_venta_local,
                'precio_ars': round(precio_final_ars, 2), 
                'precio_usd': round(precio_final_usd, 2),
                'items_adicionales_count': len(adicionales_para_db),
                'observaciones': observaciones_venta
            })
            
            db_conn.commit()
            flash(f"Presupuesto creado exitosamente por u$d {precio_final_usd:.2f}.", 'success')
            return redirect(url_for('listar_presupuestos_venta'))
            
        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error presupuesto venta: {e}", exc_info=True)
            flash(f'Error inesperado: {e}', 'danger')
            return redirect(url_for('cotizar_venta', celular_id=celular_id))
    
    clientes = db_query("SELECT * FROM personas WHERE es_cliente = 1 ORDER BY apellido, nombre, razon_social")
    return render_template('ventas/nueva.html', celular=celular, clientes=clientes, form_data={})



# =================================================================
# === MÓDULO DE SERVICIO TÉCNICO =================================
# =================================================================
@app.route('/servicio_tecnico/repuestos')
@login_required
#@admin_required
@tecnico_required
def inventario_repuestos():
    filtro_nombre = request.args.get('nombre', '').strip()
    filtro_modelo = request.args.get('modelo', '').strip()
    filtro_stock = request.args.get('stock', '').strip()
    # --- NUEVO FILTRO: Categoría ---
    filtro_categoria = request.args.get('categoria', '').strip()

    query = "SELECT * FROM repuestos WHERE 1=1"
    params = []

    if filtro_nombre:
        query += " AND nombre_parte LIKE ?"
        params.append(f"%{filtro_nombre}%")
    if filtro_modelo:
        query += " AND modelo_compatible LIKE ?"
        params.append(f"%{filtro_modelo}%")
    
    # --- Lógica de filtrado por categoría ---
    if filtro_categoria:
        query += " AND categoria = ?"
        params.append(filtro_categoria)

    if filtro_stock == 'bajo':
        query += " AND stock <= 5 AND stock > 0" # Límite de stock bajo
    elif filtro_stock == 'sin':
        query += " AND stock = 0"

    query += " ORDER BY nombre_parte"
    
    repuestos = db_query(query, tuple(params))
    
    return render_template('servicio_tecnico/repuestos.html', repuestos=repuestos,
                           filtros_activos={
                               'nombre': filtro_nombre, 
                               'modelo': filtro_modelo, 
                               'stock': filtro_stock,
                               'categoria': filtro_categoria # Pasamos el filtro nuevo al template
                           })
    
    
    
## MODIFICACIÓN IMPORTANTE DE LA FUNCIÓN crear_presupuesto_reparacion ##
# ... (otras importaciones existentes) ...

# --- RUTAS DE SERVICIO TÉCNICO (CREAR/EDITAR PRESUPUESTO) ---

# Renombrar 'nueva_reparacion.html' a 'form_reparacion.html' y crear un wrapper para `crear`
@app.route('/servicio_tecnico/presupuesto/nuevo', methods=['GET', 'POST'])
@login_required
@tecnico_required
def crear_presupuesto_reparacion():
    """
    Ruta para crear un nuevo presupuesto de servicio.
    Delega la lógica del formulario a _handle_presupuesto_reparacion_form.
    """
    return _handle_presupuesto_reparacion_form(is_edit=False)

@app.route('/servicio_tecnico/presupuesto/editar/<int:servicio_id>', methods=['GET', 'POST'])
@login_required
def editar_presupuesto_reparacion(servicio_id):
    """
    Ruta para editar un presupuesto de servicio existente.
    Delega la lógica del formulario a _handle_presupuesto_reparacion_form.
    """
    return _handle_presupuesto_reparacion_form(servicio_id=servicio_id, is_edit=True)




   

def _handle_presupuesto_reparacion_form(servicio_id=None, is_edit=False):
    """
    Función auxiliar para manejar la lógica de creación y edición de presupuestos de reparación.
    Reutiliza el mismo formulario para ambos casos e integra la gestión del Técnico (Tipeado)
    y la selección de cotización de dólar para insumos. 
    (Se eliminó el manejo de comisión en esta etapa por requerimiento).
    """
    dolar_info_from_context = inject_dolar_values()
    # Fallback del dólar de venta general
    valor_dolar_venta_local = dolar_info_from_context['valor_dolar_venta'] or 1.0
    
    # OBTENER NOMBRES DE TÉCNICOS USADOS ANTERIORMENTE PARA SUGERENCIAS (DATALIST)
    sugerencias_tecnicos = db_query("SELECT DISTINCT tecnico_nombre FROM servicios_reparacion WHERE tecnico_nombre IS NOT NULL ORDER BY tecnico_nombre ASC")

    # La consulta a repuestos ahora debe traer precio_venta_usd y precio_venta_ars
    repuestos_raw = db_query("SELECT id, nombre_parte, modelo_compatible, stock, costo_usd, precio_venta_ars, precio_venta_usd FROM repuestos ORDER BY nombre_parte")
    repuestos_for_json = [dict(r) for r in repuestos_raw] # Convertir a lista de dicts para JSON
    
    form_data = {} # Inicializar form_data para la plantilla

    # Lógica para GET request (cargar el formulario, ya sea nuevo o para edición)
    if request.method == 'GET':
        if is_edit:
            # Asegurarse de que el servicio_id exista y esté en estado 'PRESUPUESTO'
            servicio_data = db_query("SELECT * FROM servicios_reparacion WHERE id = ? AND status = 'PRESUPUESTO'", (servicio_id,))
            if not servicio_data:
                flash("Presupuesto de servicio no encontrado o no puede ser editado en su estado actual (solo se editan 'PRESUPUESTOS').", "danger")
                return redirect(url_for('listar_presupuestos_reparacion'))
            
            servicio = servicio_data[0] # Tomar la primera (y única) fila
            
            # Traemos los ítems vinculados (Stock y Manuales) usando un LEFT JOIN para obtener nombres de stock
            items_usados = db_query("""
                SELECT ru.repuesto_id, ru.manual_item_nombre, ru.cantidad, ru.costo_usd_momento,
                       r.nombre_parte, r.modelo_compatible
                FROM repuestos_usados ru
                LEFT JOIN repuestos r ON ru.repuesto_id = r.id
                WHERE ru.servicio_id = ?
            """, (servicio_id,))
            
            # Rellenar form_data con los datos del servicio existente
            form_data = {
                'servicio_id': servicio['id'], 
                'cliente_id': servicio['cliente_id'],
                'tecnico_nombre': servicio['tecnico_nombre'] or '',
                'tipo_servicio': servicio['tipo_servicio'],
                'imei_equipo': servicio['imei_equipo'] or '', 
                'falla_reportada': servicio['falla_reportada'] or '',
                'solucion_aplicada': servicio['solucion_aplicada'] or '',
                'precio_mano_obra_ars': f"{servicio['precio_mano_obra_ars']:.2f}",
                'repuesto_stock_id[]': [],
                'repuesto_nombre_display[]': [],
                'cantidad_stock[]': [],
                'precio_venta_usd_stock[]': [], 
                'manual_item_nombre[]': [],      # Lista para nombres de ítems manuales
                'cantidad_manual[]': [],         # Lista para cantidades manuales
                'precio_venta_usd_manual[]': []  # Lista para precios de venta manuales
            }
            
            for item in items_usados:
                if item['repuesto_id']: # SI TIENE ID, ES UN ÍTEM DE STOCK
                    form_data['repuesto_stock_id[]'].append(item['repuesto_id'])
                    # Construimos el nombre descriptivo para mostrar en el formulario
                    nombre_repuesto = f"{item['nombre_parte']} ({item['modelo_compatible'] or 'Genérico'})"
                    form_data['repuesto_nombre_display[]'].append(nombre_repuesto)
                    form_data['cantidad_stock[]'].append(item['cantidad'])
                    form_data['precio_venta_usd_stock[]'].append(f"{item['costo_usd_momento']:.2f}") 
                else: # SI NO TIENE ID, ES UN ÍTEM INGRESADO MANUALMENTE
                    # Verificamos que el nombre manual no sea None
                    nombre_manual = item['manual_item_nombre'] if item['manual_item_nombre'] else "Ítem manual"
                    form_data['manual_item_nombre[]'].append(nombre_manual)
                    form_data['cantidad_manual[]'].append(item['cantidad'])
                    form_data['precio_venta_usd_manual[]'].append(f"{item['costo_usd_momento']:.2f}")
        else:
            # Rellenar form_data para un presupuesto NUEVO
            form_data = {
                'precio_mano_obra_ars': '0.00', 
                'tipo_servicio': '', 
                'tecnico_nombre': '',
                'manual_item_nombre[]': [], # Inicializamos listas vacías para evitar errores de Jinja2
                'cantidad_manual[]': [],
                'precio_venta_usd_manual[]': []
            }
            if request.args.get('cliente_id'):
                form_data['cliente_id'] = int(request.args.get('cliente_id'))
                
    # Lógica para POST request (procesar el formulario enviado)
    elif request.method == 'POST':
        db_conn = get_db()
        try:
            form_data_raw = request.form.to_dict(flat=False) 

            # --- NUEVA LÓGICA DE SELECCIÓN DE DÓLAR PARA LOS INSUMOS ---
            tipo_dolar_elegido = request.form.get('tipo_dolar', 'blue')
            if tipo_dolar_elegido == 'oficial':
                valor_dolar_servicio = float(dolar_info_from_context.get('valor_bcra_venta') or 1.0)
            elif tipo_dolar_elegido == 'manual':
                valor_dolar_servicio = float(request.form.get('valor_dolar_manual') or 1.0)
            else: # blue
                valor_dolar_servicio = float(dolar_info_from_context.get('valor_blue_venta') or 1.0)

            # Captura de campos principales del formulario
            cliente_id = int(form_data_raw.get('cliente_id', [''])[0]) if form_data_raw.get('cliente_id', [''])[0] else None
            tecnico_nombre = form_data_raw.get('tecnico_nombre', [''])[0].strip()
            tipo_servicio = form_data_raw.get('tipo_servicio', [''])[0].strip()
            imei_equipo = form_data_raw.get('imei_equipo', [''])[0].strip() if form_data_raw.get('imei_equipo') else ''
            falla_reportada = form_data_raw.get('falla_reportada', [''])[0].strip() if form_data_raw.get('falla_reportada') else ''
            solucion_aplicada = form_data_raw.get('solucion_aplicada', [''])[0].strip() if form_data_raw.get('solucion_aplicada') else ''
            precio_mano_obra_ars_str = form_data_raw.get('precio_mano_obra_ars', ['0.00'])[0]
            precio_mano_obra_ars = float(precio_mano_obra_ars_str) if precio_mano_obra_ars_str else 0.0
            
            # --- SE ELIMINÓ LA CAPTURA DE COMISIÓN ---

            # --- Validaciones de Backend ---
            clientes = db_query("SELECT * FROM personas WHERE es_cliente = 1 ORDER BY apellido, nombre, razon_social")
            if not cliente_id or not tecnico_nombre or not tipo_servicio or not falla_reportada:
                flash("Todos los campos obligatorios deben ser completados.", "danger")
                return render_template('servicio_tecnico/form_reparacion.html', clientes=clientes, sugerencias_tecnicos=sugerencias_tecnicos, repuestos=json.dumps(repuestos_for_json), form_data=form_data_raw, is_edit=is_edit, servicio_id=servicio_id)

            items_para_registrar = []
            total_precio_venta_items_usd = 0.0 
            has_any_valid_item = False
            
            db_conn.execute("BEGIN TRANSACTION")

            # --- Procesar ítems de STOCK ---
            repuesto_stock_ids = form_data_raw.get('repuesto_stock_id[]', [])
            cantidades_stock = form_data_raw.get('cantidad_stock[]', [])
            precios_venta_usd_stock = form_data_raw.get('precio_venta_usd_stock[]', []) 

            for i in range(len(repuesto_stock_ids)):
                r_id_str = repuesto_stock_ids[i]
                r_id = int(r_id_str) if r_id_str and r_id_str.isdigit() else None
                cant = int(cantidades_stock[i]) if i < len(cantidades_stock) and cantidades_stock[i].isdigit() else 0
                pv_usd = float(precios_venta_usd_stock[i]) if i < len(precios_venta_usd_stock) and precios_venta_usd_stock[i] else 0.0 

                if r_id and cant > 0:
                    items_para_registrar.append({'repuesto_id': r_id, 'manual_item_nombre': None, 'cantidad': cant, 'costo_usd_momento': pv_usd})
                    total_precio_venta_items_usd += pv_usd * cant
                    has_any_valid_item = True
            
            # --- Procesar ítems MANUALES ---
            manual_nombres = form_data_raw.get('manual_item_nombre[]', [])
            manual_cantidades = form_data_raw.get('cantidad_manual[]', [])
            manual_precios = form_data_raw.get('precio_venta_usd_manual[]', []) 

            for i in range(len(manual_nombres)):
                n = manual_nombres[i].strip()
                c = int(manual_cantidades[i]) if i < len(manual_cantidades) and manual_cantidades[i].isdigit() else 0
                p = float(manual_precios[i]) if i < len(manual_precios) and manual_precios[i] else 0.0
                
                if n and c > 0:
                    items_para_registrar.append({'repuesto_id': None, 'manual_item_nombre': n, 'cantidad': c, 'costo_usd_momento': p})
                    total_precio_venta_items_usd += p * c
                    has_any_valid_item = True

            if not has_any_valid_item:
                flash("Debe añadir al menos un ítem (Repuesto o Insumo).", "danger")
                db_conn.rollback()
                return render_template('servicio_tecnico/form_reparacion.html', clientes=clientes, sugerencias_tecnicos=sugerencias_tecnicos, repuestos=json.dumps(repuestos_for_json), form_data=form_data_raw, is_edit=is_edit, servicio_id=servicio_id)

            # --- CÁLCULO FINAL USANDO LA COTIZACIÓN ELEGIDA ---
            precio_final_ars = (total_precio_venta_items_usd * valor_dolar_servicio) + precio_mano_obra_ars
            
            fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if is_edit:
                # SE QUITÓ comision_pct DEL UPDATE
                db_execute_func(db_conn,
                    """UPDATE servicios_reparacion SET 
                        cliente_id = ?, tecnico_nombre = ?, imei_equipo = ?, falla_reportada = ?, solucion_aplicada = ?, 
                        costo_total_repuestos_usd = ?, precio_mano_obra_ars = ?, precio_final_ars = ?, 
                        fecha_servicio = ?, tipo_servicio = ?
                       WHERE id = ?""",
                    (cliente_id, tecnico_nombre, imei_equipo, falla_reportada, solucion_aplicada, 
                     total_precio_venta_items_usd, precio_mano_obra_ars, precio_final_ars, 
                     fecha_actual, tipo_servicio, servicio_id)
                )
                db_execute_func(db_conn, "DELETE FROM repuestos_usados WHERE servicio_id = ?", (servicio_id,))
            else:
                # SE QUITÓ comision_pct DEL INSERT
                servicio_id = db_execute_func(db_conn,
                    """INSERT INTO servicios_reparacion 
                       (cliente_id, tecnico_nombre, imei_equipo, falla_reportada, solucion_aplicada, 
                        costo_total_repuestos_usd, precio_mano_obra_ars, precio_final_ars, fecha_servicio, status, tipo_servicio) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PRESUPUESTO', ?)""",
                    (cliente_id, tecnico_nombre, imei_equipo, falla_reportada, solucion_aplicada, 
                     total_precio_venta_items_usd, precio_mano_obra_ars, precio_final_ars, fecha_actual, tipo_servicio),
                    return_id=True
                )

            # Guardar ítems asociados
            for item in items_para_registrar:
                db_execute_func(db_conn, "INSERT INTO repuestos_usados (servicio_id, repuesto_id, manual_item_nombre, cantidad, costo_usd_momento) VALUES (?, ?, ?, ?, ?)", 
                                (servicio_id, item['repuesto_id'], item['manual_item_nombre'], item['cantidad'], item['costo_usd_momento']))
            
            db_conn.commit()
            flash(f'Servicio guardado exitosamente. Cotización aplicada: ${valor_dolar_servicio}', 'success')
            return redirect(url_for('listar_presupuestos_reparacion'))
        
        except Exception as e:
            db_conn.rollback()
            app.logger.error(f"Error presupuesto servicio: {e}", exc_info=True)
            flash(f'Ocurrió un error: {e}', 'danger')
            return redirect(url_for('crear_presupuesto_reparacion'))
    
    # Render final para GET o errores de validación
    clientes = db_query("SELECT * FROM personas WHERE es_cliente = 1 ORDER BY apellido, nombre, razon_social")
    title = "Editar Presupuesto de Servicio" if is_edit else "Nuevo Presupuesto de Servicio"
    
    return render_template('servicio_tecnico/form_reparacion.html', 
                           clientes=clientes, 
                           sugerencias_tecnicos=sugerencias_tecnicos, 
                           repuestos=json.dumps(repuestos_for_json),
                           form_data=form_data, 
                           is_edit=is_edit,
                           servicio_id=servicio_id,
                           title=title)
    
      
        
    # ... (el resto de tu archivo app.py continúa aquí) ...
@app.route('/presupuestos/reparaciones') 
@login_required
@tecnico_required
def listar_presupuestos_reparacion():
    filtro_cliente = request.args.get('cliente', '')
    filtro_imei = request.args.get('imei', '').strip()
    filtro_tipo_servicio = request.args.get('tipo_servicio', '')

    query = "SELECT s.*, p.nombre, p.apellido, p.razon_social FROM servicios_reparacion s JOIN personas p ON s.cliente_id = p.id WHERE s.status = 'PRESUPUESTO'"
    params = []

    if filtro_cliente:
        query += " AND p.id = ?"
        params.append(filtro_cliente)
    if filtro_imei:
        query += " AND s.imei_equipo LIKE ?"
        params.append(f"%{filtro_imei}%")
    if filtro_tipo_servicio:
        query += " AND s.tipo_servicio = ?"
        params.append(filtro_tipo_servicio)


    query += " ORDER BY s.fecha_servicio DESC"
    presupuestos = db_query(query, tuple(params))
    
    clientes_disponibles = db_query("SELECT * FROM personas WHERE es_cliente = 1 ORDER BY razon_social, apellido")

    return render_template('presupuestos/listar_reparaciones.html', 
                           presupuestos=presupuestos,
                           filtros_activos={'cliente': filtro_cliente, 'imei': filtro_imei, 'tipo_servicio': filtro_tipo_servicio},
                           clientes_disponibles=clientes_disponibles)


@app.route('/servicio_tecnico/reparaciones/historial')
@login_required
@tecnico_required
def listar_reparaciones_completadas():
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_cliente = request.args.get('cliente', '')
    filtro_imei = request.args.get('imei', '').strip()
    filtro_tipo_servicio = request.args.get('tipo_servicio', '')

    query = "SELECT s.*, p.nombre, p.apellido, p.razon_social FROM servicios_reparacion s JOIN personas p ON s.cliente_id = p.id WHERE s.status = 'COMPLETADO' AND s.fecha_servicio BETWEEN ? AND ?"
    params = [start_date, end_date_query]

    if filtro_cliente:
        query += " AND p.id = ?"
        params.append(filtro_cliente)
    if filtro_imei:
        query += " AND s.imei_equipo LIKE ?"
        params.append(f"%{filtro_imei}%")
    if filtro_tipo_servicio:
        query += " AND s.tipo_servicio = ?"
        params.append(filtro_tipo_servicio)

    query += " ORDER BY s.fecha_servicio DESC"
    reparaciones = db_query(query, tuple(params))
    
    clientes_disponibles = db_query("SELECT * FROM personas WHERE es_cliente = 1 ORDER BY razon_social, apellido")

    return render_template('servicio_tecnico/reparaciones.html', reparaciones=reparaciones, titulo="Historial de Servicios Completados", 
                           start_date=start_date, end_date=end_date_display,
                           filtros_activos={'cliente': filtro_cliente, 'imei': filtro_imei, 'tipo_servicio': filtro_tipo_servicio},
                           clientes_disponibles=clientes_disponibles)

@app.route('/servicio_tecnico/reparacion/<int:servicio_id>')
@login_required
def view_reparacion(servicio_id):
    reparacion_data = db_query("SELECT s.*, p.nombre, p.apellido, p.razon_social, p.cuit_cuil FROM servicios_reparacion s JOIN personas p ON s.cliente_id = p.id WHERE s.id = ?", (servicio_id,))
    if not reparacion_data:
        flash("Orden de reparación/presupuesto no encontrada.", "danger")
        return redirect(url_for('listar_presupuestos_reparacion'))
    
    # MODIFICACIÓN: En view_reparacion, la columna costo_usd_momento ahora contiene el precio de venta.
    # El alias `precio_venta_usd_momento` es para claridad en la plantilla.
    items_usados = db_query("SELECT ru.cantidad, ru.costo_usd_momento AS precio_venta_usd_momento, ru.manual_item_nombre, r.nombre_parte, r.modelo_compatible FROM repuestos_usados ru LEFT JOIN repuestos r ON ru.repuesto_id = r.id WHERE ru.servicio_id = ?", (servicio_id,))
    
    return render_template('servicio_tecnico/view_reparacion.html', reparacion=reparacion_data[0], items=items_usados)

# =================================================================
# === MÓDULO DE PRESUPUESTOS ======================================
# =================================================================
@app.route('/presupuestos')
@login_required
def menu_presupuestos():
    return render_template('presupuestos/menu.html')

@app.route('/presupuestos/ventas')
@login_required
def listar_presupuestos_venta():
    filtro_cliente = request.args.get('cliente', '')
    filtro_producto = request.args.get('producto', '').strip()

    query = "SELECT v.*, c.marca, c.modelo, c.imei, p.nombre, p.apellido, p.razon_social FROM ventas v JOIN celulares c ON v.celular_id = c.id JOIN personas p ON v.cliente_id = p.id WHERE v.status = 'PRESUPUESTO'"
    params = []

    if filtro_cliente:
        query += " AND p.id = ?"
        params.append(filtro_cliente)
    if filtro_producto:
        query += " AND (c.marca LIKE ? OR c.modelo LIKE ? OR c.imei LIKE ?)"
        params.extend([f"%{filtro_producto}%", f"%{filtro_producto}%", f"%{filtro_producto}%"])

    query += " ORDER BY v.fecha_venta DESC"
    presupuestos = db_query(query, tuple(params))
    
    clientes_disponibles = db_query("SELECT * FROM personas WHERE es_cliente = 1 ORDER BY razon_social, apellido")

    return render_template('presupuestos/listar_ventas.html', presupuestos=presupuestos,
                           filtros_activos={'cliente': filtro_cliente, 'producto': filtro_producto},
                           clientes_disponibles=clientes_disponibles)

## NUEVA FUNCIONALIDAD: PARTE DE PAGO ##
# MODIFICADO: Esta ruta ahora mostrará el formulario de pago
@app.route('/presupuestos/ventas/confirmar/<int:venta_id>', methods=['GET'])
@login_required
def mostrar_formulario_pago(venta_id):
    venta = db_query("SELECT v.*, c.marca, c.modelo, c.imei, p.nombre, p.apellido, p.razon_social, p.cuit_cuil FROM ventas v JOIN celulares c ON v.celular_id = c.id JOIN personas p ON v.cliente_id = p.id WHERE v.id = ? AND v.status = 'PRESUPUESTO'", (venta_id,))
    if not venta:
        flash("El presupuesto de venta no existe o ya fue procesado.", "danger")
        return redirect(url_for('listar_presupuestos_venta'))
    
    form_data = request.args.to_dict(flat=True) if request.method == 'GET' and 'monto_efectivo_ars' in request.args else {}
    
    return render_template('ventas/confirmar_pago.html', 
                           venta=venta[0], 
                           form_data=form_data)


@app.route('/ventas/procesar_pago/<int:venta_id>', methods=['POST'])
@login_required
def procesar_pago(venta_id):
    db_conn = get_db()
    try:
        db_conn.execute("BEGIN TRANSACTION")

        venta = db_query_func(db_conn, "SELECT v.*, c.imei FROM ventas v JOIN celulares c ON v.celular_id = c.id WHERE v.id = ? AND v.status = 'PRESUPUESTO'", (venta_id,))
        if not venta:
            flash("El presupuesto de venta no existe o ya fue procesado.", "danger")
            return redirect(url_for('listar_presupuestos_venta'))
        venta = venta[0]

        # Validaciones para el celular a vender
        celular_vendido = db_query_func(db_conn, "SELECT stock FROM celulares WHERE id = ?", (venta['celular_id'],))[0]
        if celular_vendido['stock'] == 0:
            flash(f"El celular con IMEI {venta['imei']} no está disponible en stock para la venta.", "danger")
            return redirect(url_for('mostrar_formulario_pago', venta_id=venta_id, **request.form.to_dict(flat=True)))

        # --- SELECCIÓN DINÁMICA DE DÓLAR PARA LA LIQUIDACIÓN ---
        dolar_info = obtener_cotizacion_dolar()
        tipo_dolar_pago = request.form.get('tipo_dolar', 'blue')
        
        if tipo_dolar_pago == 'oficial':
            valor_dolar_pago = float(dolar_info['venta'] or 1.0)
        elif tipo_dolar_pago == 'manual':
            valor_dolar_pago = float(request.form.get('valor_dolar_manual') or 1.0)
        else: # blue
            valor_dolar_pago = float(dolar_info['venta_blue'] or 1.0)

        if valor_dolar_pago <= 0:
            raise ValueError("La cotización del dólar de pago debe ser mayor a 0.")

        # --- CAPTURA DE CUENTAS DE DESTINO ---
        cuenta_destino_virtual = request.form.get('cuenta_destino_virtual', 'BANCO')

        # --- Recoger datos de pago del formulario ---
        monto_efectivo_ars = float(request.form.get('monto_efectivo_ars', 0) or 0)
        monto_efectivo_usd = float(request.form.get('monto_efectivo_usd', 0) or 0)
        monto_transferencia_ars = float(request.form.get('monto_transferencia_ars', 0) or 0)
        monto_debito_ars = float(request.form.get('monto_debito_ars', 0) or 0)
        monto_credito_ars = float(request.form.get('monto_credito_ars', 0) or 0)
        monto_mp_ars = float(request.form.get('monto_mp_ars', 0) or 0)
        monto_virtual_usd = float(request.form.get('monto_virtual_usd', 0) or 0)
        
        usar_parte_pago = 'usar_parte_pago' in request.form
        celular_parte_pago_id = request.form.get('celular_parte_pago_id') if usar_parte_pago else None
        
        # MODIFICACIÓN: Capturamos el valor directamente en USD desde el nuevo input del HTML
        valor_pp_usd = float(request.form.get('valor_celular_parte_pago_usd', 0) or 0) if usar_parte_pago else 0
        
        if usar_parte_pago and not celular_parte_pago_id:
            flash("Debe seleccionar un celular para la parte de pago.", "danger")
            return redirect(url_for('mostrar_formulario_pago', venta_id=venta_id, **request.form.to_dict(flat=True)))

        # --- CÁLCULOS FINANCIEROS EN DÓLARES ---
        total_a_cobrar_usd = venta['precio_final_usd']
        
        # Sumamos los montos que ya vienen en USD (Efectivo, Virtual y Parte de Pago)
        total_pagado_usd = monto_efectivo_usd + monto_virtual_usd + valor_pp_usd
        
        # Sumamos los montos en ARS convertidos a USD según la cotización elegida
        total_pagado_usd += (monto_efectivo_ars / valor_dolar_pago)
        total_pagado_usd += (monto_transferencia_ars / valor_dolar_pago)
        total_pagado_usd += (monto_debito_ars / valor_dolar_pago)
        total_pagado_usd += (monto_credito_ars / valor_dolar_pago)
        total_pagado_usd += (monto_mp_ars / valor_dolar_pago)
        
        # Calculamos la diferencia
        diferencia_usd = total_pagado_usd - total_a_cobrar_usd
        saldo_pendiente_usd = 0.0

        if diferencia_usd > 0.05: # Margen pequeño por redondeo
            flash(f"Error: El monto pagado (u$d {total_pagado_usd:.2f}) excede el total (u$d {total_a_cobrar_usd:.2f}).", "danger")
            return redirect(url_for('mostrar_formulario_pago', venta_id=venta_id, **request.form.to_dict(flat=True)))
        elif diferencia_usd < -0.01:
            saldo_pendiente_usd = abs(diferencia_usd)

        # --- LÓGICA DE CUOTAS EN USD ---
        cantidad_cuotas = 1
        if saldo_pendiente_usd > 0.01:
            cantidad_cuotas = int(request.form.get('cantidad_cuotas', 1))
            intervalo_dias = int(request.form.get('intervalo_dias', 30))
            
            cuota_usd = saldo_pendiente_usd / cantidad_cuotas
            monto_cuota_ars = cuota_usd * valor_dolar_pago 
            
            fecha_actual = datetime.now()
            for i in range(1, cantidad_cuotas + 1):
                fecha_vencimiento = (fecha_actual + timedelta(days=i * intervalo_dias)).strftime('%Y-%m-%d')
                
                # Busca la parte donde haces el INSERT en ventas_cuotas y cámbialo por esto:
                db_execute_func(db_conn, """
                    INSERT INTO ventas_cuotas (venta_id, numero_cuota, monto_ars, monto_original_ars, fecha_vencimiento, estado)
                    VALUES (?, ?, ?, ?, ?, 'PENDIENTE')
                """, (venta_id, i, monto_cuota_ars, monto_cuota_ars, fecha_vencimiento)) 
                # Guardamos lo mismo en monto_ars (que será el saldo que baja) 
                # y en monto_original_ars (que no se toca)
                
                #db_execute_func(db_conn, """
                #    INSERT INTO ventas_cuotas (venta_id, numero_cuota, monto_ars, fecha_vencimiento, estado)
                #    VALUES (?, ?, ?, ?, 'PENDIENTE')
                #""", (venta_id, i, monto_cuota_ars, fecha_vencimiento))

        # --- Procesar el celular en parte de pago ---
        if usar_parte_pago and celular_parte_pago_id:
            # MODIFICACIÓN: El costo_usd del equipo que reingresa es directamente el valor acordado en USD
            db_execute_func(db_conn, """
                UPDATE celulares SET 
                    stock = 1, es_parte_pago = 1, costo_usd = ?, 
                    observaciones = COALESCE(observaciones, '') || ? 
                WHERE id = ?
            """, (valor_pp_usd, f", Reingreso Venta ID {venta_id}", celular_parte_pago_id))

        # --- Actualizar la venta ---
        # Calculamos los equivalentes en ARS para las columnas de la tabla ventas (referencia histórica)
        valor_pp_ars_historico = valor_pp_usd * valor_dolar_pago
        saldo_pendiente_ars = saldo_pendiente_usd * valor_dolar_pago

        db_execute_func(db_conn, """
            UPDATE ventas 
            SET status = 'COMPLETADA', 
                monto_cobrado_ars = ?, 
                monto_cobrado_usd = ?,
                monto_transferencia_ars = ?, 
                monto_debito_ars = ?, 
                monto_credito_ars = ?, 
                monto_mp_ars = ?,
                monto_virtual_usd = ?, 
                celular_parte_pago_id = ?, 
                valor_celular_parte_pago = ?, 
                saldo_pendiente = ?,
                valor_dolar_momento = ?, 
                cantidad_cuotas = ?
            WHERE id = ?
        """, (
            monto_efectivo_ars, 
            monto_efectivo_usd, 
            monto_transferencia_ars, 
            monto_debito_ars, 
            monto_credito_ars, 
            monto_mp_ars,
            monto_virtual_usd, # <-- Campo agregado
            celular_parte_pago_id, 
            valor_pp_ars_historico, 
            saldo_pendiente_ars, 
            valor_dolar_pago, 
            cantidad_cuotas, 
            venta_id
        ))
        
        db_execute_func(db_conn, "UPDATE celulares SET stock = 0 WHERE id = ?", (venta['celular_id'],))
        
        
        # ==========================================================
        # === DESCUENTO DE ÍTEMS ADICIONALES (Venta Accesorios) ===
        # ==========================================================
        adicionales = db_query_func(db_conn, "SELECT repuesto_id, cantidad FROM items_adicionales_venta WHERE venta_id = ?", (venta_id,))
        for item in adicionales:
            repuesto_data = db_query_func(db_conn, "SELECT stock, nombre_parte FROM repuestos WHERE id = ?", (item['repuesto_id'],))
            if repuesto_data:
                stock_actual = repuesto_data[0]['stock']
                nombre_repuesto = repuesto_data[0]['nombre_parte']
                
                if stock_actual < item['cantidad']:
                    db_conn.rollback()
                    flash(f"Error: No hay stock suficiente del accesorio '{nombre_repuesto}' (Disponible: {stock_actual}).", "danger")
                    return redirect(url_for('mostrar_formulario_pago', venta_id=venta_id))
                
                db_execute_func(db_conn, "UPDATE repuestos SET stock = stock - ? WHERE id = ?", (item['cantidad'], item['repuesto_id']))

        # --- DESCUENTO DE REGALOS / PROMOCIONES ---
        regalos = db_query_func(db_conn, "SELECT repuesto_id, cantidad FROM items_promocionales_venta WHERE venta_id = ?", (venta_id,))
        for reg in regalos:
            repuesto_data = db_query_func(db_conn, "SELECT stock, nombre_parte FROM repuestos WHERE id = ?", (reg['repuesto_id'],))
            if repuesto_data:
                stock_actual = repuesto_data[0]['stock']
                nombre_repuesto = repuesto_data[0]['nombre_parte']
                if stock_actual < reg['cantidad']:
                    db_conn.rollback()
                    flash(f"Error: No hay stock suficiente de '{nombre_repuesto}' para el regalo.", "danger")
                    return redirect(url_for('mostrar_formulario_pago', venta_id=venta_id))
        
                db_execute_func(db_conn, "UPDATE repuestos SET stock = stock - ? WHERE id = ?", (reg['cantidad'], reg['repuesto_id']))
        
        # --- REGISTROS DE CAJA SEPARADOS ---
        if monto_efectivo_usd > 0:
            registrar_movimiento_caja(current_user.id, 'INGRESO_VENTA_USD', 0, monto_efectivo_usd, f"Venta #{venta_id} USD Billete (Caja)", venta_id, None, 'EFECTIVO')
        
        if monto_efectivo_ars > 0:
            registrar_movimiento_caja(current_user.id, 'INGRESO_VENTA', monto_efectivo_ars, 0, f"Venta #{venta_id} ARS Efectivo (Caja)", venta_id, None, 'EFECTIVO')
        
        total_v_ars = monto_transferencia_ars + monto_mp_ars + monto_debito_ars + monto_credito_ars
        if total_v_ars > 0:
            registrar_movimiento_caja(current_user.id, 'INGRESO_VENTA_VIRTUAL_ARS', total_v_ars, 0, f"Venta #{venta_id} Pago Virtual ARS", venta_id, None, cuenta_destino_virtual)

        if monto_virtual_usd > 0:
            registrar_movimiento_caja(current_user.id, 'INGRESO_VENTA_VIRTUAL_USD', 0, monto_virtual_usd, f"Venta #{venta_id} Pago Virtual USD", venta_id, None, cuenta_destino_virtual)

        registrar_movimiento(current_user.id, 'VENTA_COBRADA', 'VENTA', venta_id, {
            'total_usd': total_a_cobrar_usd,
            'saldo_pendiente_usd': saldo_pendiente_usd,
            'cotizacion_venta': valor_dolar_pago,
            'valor_toma_usd': valor_pp_usd
        })
        
        db_conn.commit()
        flash(f"Venta confirmada. Saldo a financiar: u$d {saldo_pendiente_usd:.2f}.", "success")
        return redirect(url_for('listar_presupuestos_venta'))

    except Exception as e:
        db_conn.rollback()
        app.logger.error(f"Error procesar pago: {e}", exc_info=True)
        flash(f"Error: {e}", "danger")
        return redirect(url_for('mostrar_formulario_pago', venta_id=venta_id))
    
      
        
@app.route('/presupuestos/reparaciones/confirmar/<int:servicio_id>', methods=['POST'])
@login_required
#@admin_required
@tecnico_required
def confirmar_reparacion(servicio_id):
    db = get_db()
    try:
        db.execute("BEGIN TRANSACTION")
        servicio = db_query_func(db, "SELECT * FROM servicios_reparacion WHERE id = ? AND status = 'PRESUPUESTO'", (servicio_id,))
        if not servicio:
            flash("El presupuesto no existe o ya fue procesado.", "danger")
            return redirect(url_for('listar_presupuestos_reparacion'))
        servicio = servicio[0]

        # Obtener los ítems asociados al servicio (tanto de stock como manuales)
        items_asociados = db_query_func(db, "SELECT ru.cantidad, ru.repuesto_id, ru.manual_item_nombre FROM repuestos_usados ru WHERE ru.servicio_id = ?", (servicio_id,))
        
        # Descontar stock para ítems que provienen del inventario (repuestos_id no NULL)
        for item in items_asociados:
            if item['repuesto_id']: # Es un ítem de stock
                repuesto_info = db_query_func(db, "SELECT stock, nombre_parte FROM repuestos WHERE id = ?", (item['repuesto_id'],))[0]
                if repuesto_info['stock'] < item['cantidad']:
                    flash(f"Stock insuficiente para '{repuesto_info['nombre_parte']}'. Necesita {item['cantidad']}, disponible {repuesto_info['stock']}.", "danger")
                    db.rollback()
                    return redirect(url_for('listar_presupuestos_reparacion'))
                db_execute_func(db, "UPDATE repuestos SET stock = stock - ? WHERE id = ?", (item['cantidad'], item['repuesto_id']))
        
        # --- MODIFICACIÓN CLAVE: Generar Deuda en Cuenta Corriente ---
        # Se cambia el status a COMPLETADO y se asigna el precio total al saldo_pendiente
        db_execute_func(db, "UPDATE servicios_reparacion SET status = 'COMPLETADO', saldo_pendiente = precio_final_ars WHERE id = ?", (servicio_id,))
        
        # Registrar movimiento de auditoría indicando que se generó una deuda
        registrar_movimiento(current_user.id, 'SERVICIO_CONFIRMADO_DEUDA', 'SERVICIO', servicio_id, {
            'precio_ars': servicio['precio_final_ars'], 
            'imei_equipo': servicio['imei_equipo'],
            'tipo_servicio': servicio['tipo_servicio'],
            'info': "Servicio terminado. Deuda cargada a Cuenta Corriente del cliente."
        })
        
        # --- NOTA: Se eliminan los llamados a registrar_movimiento_caja ---
        # El dinero no ingresa a caja todavía. Ingresará cuando el usuario use el módulo
        # de Cuenta Corriente para "Cobrar Cliente", permitiendo elegir el método de pago.
        
        db.commit()
        flash("Servicio confirmado. El monto se ha cargado a la cuenta corriente del cliente correctamente.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error al confirmar el servicio: {e}", "danger")
        app.logger.error(f"Error al confirmar servicio {servicio_id}: {e}", exc_info=True)
    return redirect(url_for('listar_presupuestos_reparacion'))




@app.route('/presupuestos/ventas/cancelar/<int:venta_id>', methods=['POST'])
@login_required
def cancelar_presupuesto_venta(venta_id):
    venta = db_query("SELECT id, celular_id, status FROM ventas WHERE id = ?", (venta_id,))
    if not venta or venta[0]['status'] != 'PRESUPUESTO':
        flash("El presupuesto no existe o no se puede cancelar en su estado actual.", "danger")
        return redirect(url_for('listar_presupuestos_venta'))

    db_execute("UPDATE ventas SET status = 'CANCELADA' WHERE id = ?", (venta_id,))
    registrar_movimiento(current_user.id, 'CANCELACION', 'VENTA', venta_id)
    flash("Presupuesto de venta cancelado.", "info")
    return redirect(url_for('listar_presupuestos_venta'))

@app.route('/presupuestos/reparaciones/cancelar/<int:servicio_id>', methods=['POST'])
@login_required
def cancelar_presupuesto_reparacion(servicio_id):
    servicio = db_query("SELECT id, status FROM servicios_reparacion WHERE id = ?", (servicio_id,))
    if not servicio or servicio[0]['status'] != 'PRESUPUESTO':
        flash("El presupuesto no existe o no se puede cancelar en su estado actual.", "danger")
        return redirect(url_for('listar_presupuestos_reparacion'))

    db_execute("UPDATE servicios_reparacion SET status = 'CANCELADO' WHERE id = ?", (servicio_id,))
    registrar_movimiento(current_user.id, 'CANCELACION', 'SERVICIO', servicio_id)
    flash("Presupuesto de reparación cancelado.", "info")
    return redirect(url_for('listar_presupuestos_reparacion'))

# --- RUTAS DE VISTA E IMPRESIÓN ---
@app.route('/ventas/ver/<int:venta_id>')
@login_required
def view_venta(venta_id):
    # 1. Consulta principal (Venta, Cliente, Equipo y Parte de Pago)
    venta_data = db_query("""
        SELECT 
            v.*, 
            c.marca, c.modelo, c.imei, c.condicion, 
            p.nombre, p.apellido, p.razon_social, p.cuit_cuil,
            cpp.marca AS pp_marca, cpp.modelo AS pp_modelo, cpp.imei AS pp_imei, cpp.condicion AS pp_condicion
        FROM ventas v
        JOIN celulares c ON v.celular_id = c.id
        JOIN personas p ON v.cliente_id = p.id
        LEFT JOIN celulares cpp ON v.celular_parte_pago_id = cpp.id
        WHERE v.id = ?
    """, (venta_id,))
    
    if not venta_data:
        flash("Presupuesto de venta no encontrado.", "danger")
        return redirect(url_for('listar_presupuestos_venta'))

    # =================================================================
    # === NUEVO: CONSULTA DE ÍTEMS PROMOCIONALES / REGALOS (PUNTO 5) ===
    # =================================================================
    regalos_entrega = db_query("""
        SELECT r.nombre_parte, r.modelo_compatible, ipv.cantidad
        FROM items_promocionales_venta ipv
        JOIN repuestos r ON ipv.repuesto_id = r.id
        WHERE ipv.venta_id = ?
    """, (venta_id,))
    # =================================================================
    
    return_url = url_for('listar_presupuestos_venta') if venta_data[0]['status'] == 'PRESUPUESTO' else url_for('reporte_actividad')
    
    # IMPORTANTE: Agregamos 'regalos=regalos_entrega' al render_template
    return render_template('ventas/view_venta.html', 
                           venta=venta_data[0], 
                           regalos=regalos_entrega, 
                           return_url=return_url)
    
    
@app.route('/ventas/imprimir/<int:venta_id>')
@login_required
def imprimir_venta(venta_id):
    # 1. Consulta principal de la venta y datos relacionados (Equipo, Cliente, Parte de Pago)
    venta_data = db_query("""
        SELECT 
            v.*, 
            c.marca, c.modelo, c.imei, c.condicion, 
            p.nombre, p.apellido, p.razon_social, p.cuit_cuil, p.telefono,
            cpp.marca AS pp_marca, cpp.modelo AS pp_modelo, cpp.imei AS pp_imei, cpp.condicion AS pp_condicion
        FROM ventas v
        JOIN celulares c ON v.celular_id = c.id
        JOIN personas p ON v.cliente_id = p.id
        LEFT JOIN celulares cpp ON v.celular_parte_pago_id = cpp.id
        WHERE v.id = ?
    """, (venta_id,))
    
    if not venta_data:
        flash("Presupuesto de venta no encontrado.", "danger")
        return redirect(url_for('listar_presupuestos_venta'))

    # 2. Consulta de Ítems Promocionales / Regalos (Se entregan sin cargo)
    regalos_entrega = db_query("""
        SELECT r.nombre_parte, r.modelo_compatible, ipv.cantidad
        FROM items_promocionales_venta ipv
        JOIN repuestos r ON ipv.repuesto_id = r.id
        WHERE ipv.venta_id = ?
    """, (venta_id,))

    # 3. Consulta de Ítems Adicionales (Accesorios vendidos junto con el equipo)
    adicionales_venta = db_query("""
        SELECT r.nombre_parte, r.modelo_compatible, iav.cantidad, iav.precio_vendido_usd
        FROM items_adicionales_venta iav
        JOIN repuestos r ON iav.repuesto_id = r.id
        WHERE iav.venta_id = ?
    """, (venta_id,))

    # Retornamos la plantilla de impresión pasando las 3 colecciones de datos
    return render_template('ventas/imprimir.html', 
                           venta=venta_data[0], 
                           regalos=regalos_entrega,
                           adicionales=adicionales_venta)
    
    
    # =================================================================
    # === NUEVO: CONSULTA DE ÍTEMS PROMOCIONALES / REGALOS (PUNTO 5) ===
    # =================================================================
    regalos_entrega = db_query("""
        SELECT r.nombre_parte, r.modelo_compatible, ipv.cantidad
        FROM items_promocionales_venta ipv
        JOIN repuestos r ON ipv.repuesto_id = r.id
        WHERE ipv.venta_id = ?
    """, (venta_id,))
    # =================================================================

    # Agregamos 'regalos=regalos_entrega' para que el HTML de impresión los reciba
    return render_template('ventas/imprimir.html', 
                           venta=venta_data[0], 
                           regalos=regalos_entrega)
    
    
# app.py (dentro de la función imprimir_reparacion)

@app.route('/servicio_tecnico/imprimir/<int:servicio_id>')
@login_required
def imprimir_reparacion(servicio_id):
    reparacion_data = db_query("SELECT s.*, p.nombre, p.apellido, p.razon_social, p.cuit_cuil, p.telefono FROM servicios_reparacion s JOIN personas p ON s.cliente_id = p.id WHERE s.id = ?", (servicio_id,))
    if not reparacion_data:
        flash("Presupuesto de reparación no encontrado.", "danger")
        return redirect(url_for('listar_presupuestos_reparacion'))
    
    # MODIFICACIÓN: En imprimir_reparacion, la columna costo_usd_momento ahora contiene el precio de venta.
    # El alias `precio_venta_usd_momento` es para claridad en la plantilla.
    items_usados = db_query("SELECT ru.cantidad, ru.costo_usd_momento AS precio_venta_usd_momento, ru.manual_item_nombre, r.nombre_parte, r.modelo_compatible FROM repuestos_usados ru LEFT JOIN repuestos r ON ru.repuesto_id = r.id WHERE ru.servicio_id = ?", (servicio_id,))
    
    fecha_emision = datetime.now() # Obtener la fecha y hora actuales

    return render_template('servicio_tecnico/imprimir.html', 
                           reparacion=reparacion_data[0], 
                           items=items_usados,
                           fecha_emision=fecha_emision) 




@app.route('/caja/movimientos', methods=['GET', 'POST'])
@login_required
@admin_required 
def movimientos_caja():
    # Buscamos si hay una caja abierta para el turno actual
    caja_abierta = db_query("SELECT * FROM arqueo_caja WHERE estado = 'ABIERTO'")
    #caja_abierta = db_query("SELECT * FROM arqueo_caja WHERE user_id = ? AND estado = 'ABIERTO'", (current_user.id,))
    if not caja_abierta:
        flash("Debes tener una caja abierta (dinero físico) para registrar movimientos.", "warning")
        return redirect(url_for('arqueo_caja'))
    
    arqueo_actual = caja_abierta[0]
    
    if request.method == 'POST':
        tipo_movimiento = request.form['tipo']
        monto_ars = float(request.form.get('monto_ars', 0) or 0)
        monto_usd = float(request.form.get('monto_usd', 0) or 0)
        descripcion = request.form['descripcion'].strip()
        sub_categoria = request.form.get('sub_categoria', '').strip()
        
        # El método de pago se toma del formulario, pero recuerda que solo lo verás aquí si eliges 'EFECTIVO'
        metodo_pago = request.form.get('metodo_pago', 'EFECTIVO').strip()

        if monto_ars <= 0 and monto_usd <= 0:
            flash("El monto debe ser positivo en ARS o USD.", "danger")
            return redirect(url_for('movimientos_caja'))
        
        if not descripcion:
            flash("La descripción es obligatoria.", "danger")
            return redirect(url_for('movimientos_caja'))
        
        if (tipo_movimiento == 'INGRESO_MANUAL' or tipo_movimiento == 'EGRESO_MANUAL') and not sub_categoria:
            flash("Para movimientos manuales, debe seleccionar un sub-rubro.", "danger")
            return redirect(url_for('movimientos_caja'))

        if tipo_movimiento not in ['INGRESO_MANUAL', 'EGRESO_MANUAL']:
            sub_categoria = None

        # --- REGISTRO BIMONEDA LIMPIO ---
        if monto_ars > 0:
            registrar_movimiento_caja(current_user.id, f"{tipo_movimiento}_ARS", 
                                      monto_ars=monto_ars, 
                                      monto_usd=0, 
                                      descripcion=descripcion, 
                                      sub_categoria=sub_categoria, 
                                      metodo_pago=metodo_pago)
        
        if monto_usd > 0:
            registrar_movimiento_caja(current_user.id, f"{tipo_movimiento}_USD", 
                                      monto_ars=0, 
                                      monto_usd=monto_usd, 
                                      descripcion=descripcion, 
                                      sub_categoria=sub_categoria, 
                                      metodo_pago=metodo_pago)

        flash(f"Movimiento registrado exitosamente.", "success")
        return redirect(url_for('movimientos_caja'))

    # --- CONSULTA FILTRADA: SOLO EFECTIVO FÍSICO ---
    # MODIFICACIÓN CLAVE: Agregamos el filtro cm.metodo_pago = 'EFECTIVO'
    movimientos = db_query("""
        SELECT cm.*, u.username 
        FROM caja_movimientos cm 
        JOIN users u ON cm.user_id = u.id 
        WHERE cm.metodo_pago = 'EFECTIVO' 
        AND cm.fecha BETWEEN ? AND ? 
        ORDER BY cm.fecha DESC
    """, (arqueo_actual['fecha_apertura'], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    # --- CÁLCULO DE TOTALES (SOLO DINERO FÍSICO) ---
    # Como la consulta de arriba ya está filtrada, estos sumatorios son 100% precisos para la caja de billetes
    total_ingresos_ars = sum(m['monto_ars'] for m in movimientos if m['monto_ars'] and 
                            ('INGRESO' in m['tipo'] or 'APERTURA' in m['tipo'] or 'VENTA' in m['tipo'] or 'COBRO' in m['tipo'] or 'SERVICIO' in m['tipo']))
    
    total_egresos_ars = sum(m['monto_ars'] for m in movimientos if m['monto_ars'] and 
                           ('EGRESO' in m['tipo'] or 'PAGO_PROVEEDOR' in m['tipo'] or 'CIERRE' in m['tipo']))

    total_ingresos_usd = sum(m['monto_usd'] for m in movimientos if m['monto_usd'] and 
                            ('INGRESO' in m['tipo'] or 'APERTURA' in m['tipo'] or 'VENTA' in m['tipo'] or 'COBRO' in m['tipo']))
    
    total_egresos_usd = sum(m['monto_usd'] for m in movimientos if m['monto_usd'] and 
                           ('EGRESO' in m['tipo'] or 'PAGO_PROVEEDOR' in m['tipo'] or 'CIERRE' in m['tipo']))

    return render_template('caja/movimientos_caja.html', 
                           movimientos=movimientos, 
                           arqueo_actual=arqueo_actual, 
                           sub_rubros=SUB_RUBROS_CAJA, 
                           form_data=request.form,
                           total_ingresos_ars=total_ingresos_ars,
                           total_egresos_ars=total_egresos_ars,
                           total_ingresos_usd=total_ingresos_usd,
                           total_egresos_usd=total_egresos_usd)
    
       
    
# ==========================================
# === FUNCIÓN ARQUEO DE CAJA (MODIFICADA) ===
# ==========================================
# ==========================================
# === FUNCIÓN ARQUEO DE CAJA (MODIFICADA) ===
# ==========================================
@app.route('/caja/arqueo', methods=['GET', 'POST'])
@login_required
@admin_required
def arqueo_caja():
    #caja_abierta = db_query("SELECT * FROM arqueo_caja WHERE user_id = ? AND estado = 'ABIERTO'", (current_user.id,))
    caja_abierta = db_query("SELECT * FROM arqueo_caja WHERE estado = 'ABIERTO'")
    # --- LÓGICA POST (APERTURA Y CIERRE) ---
    if request.method == 'POST':
        if 'abrir_caja' in request.form:
            if caja_abierta:
                flash("Ya tienes una caja abierta.", "warning")
                return redirect(url_for('arqueo_caja'))
            
            monto_inicial_ars = float(request.form.get('monto_inicial_ars', 0) or 0)
            monto_inicial_usd = float(request.form.get('monto_inicial_usd', 0) or 0)

            if monto_inicial_ars < 0 or monto_inicial_usd < 0:
                flash("Los montos iniciales no pueden ser negativos.", "danger")
                return redirect(url_for('arqueo_caja'))
            
            fecha_apertura = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            arqueo_id = db_execute("INSERT INTO arqueo_caja (user_id, fecha_apertura, monto_inicial_ars, monto_inicial_usd, estado) VALUES (?, ?, ?, ?, 'ABIERTO')",
                                   (current_user.id, fecha_apertura, monto_inicial_ars, monto_inicial_usd), return_id=True)
            
            # Apertura siempre es EFECTIVO físico
            if monto_inicial_ars > 0:
                registrar_movimiento_caja(current_user.id, 'APERTURA_CAJA_ARS', monto_ars=monto_inicial_ars, monto_usd=0, descripcion="Monto inicial ARS", referencia_id=arqueo_id, metodo_pago='EFECTIVO')
            if monto_inicial_usd > 0:
                registrar_movimiento_caja(current_user.id, 'APERTURA_CAJA_USD', monto_ars=0, monto_usd=monto_inicial_usd, descripcion="Monto inicial USD", referencia_id=arqueo_id, metodo_pago='EFECTIVO')
            
            registrar_movimiento(current_user.id, 'APERTURA', 'ARQUEO_CAJA', arqueo_id, {'monto_inicial_ars': monto_inicial_ars, 'monto_inicial_usd': monto_inicial_usd})
            flash(f"Caja abierta correctamente.", "success")
            return redirect(url_for('arqueo_caja'))

        elif 'cerrar_caja' in request.form:
            if not caja_abierta:
                flash("No tienes una caja abierta para cerrar.", "danger")
                return redirect(url_for('arqueo_caja'))
            
            arqueo_id = caja_abierta[0]['id']
            f_ap = caja_abierta[0]['fecha_apertura']
            monto_contado_fisico_ars = float(request.form.get('monto_contado_fisico_ars', 0) or 0)
            monto_contado_fisico_usd = float(request.form.get('monto_contado_fisico_usd', 0) or 0)
            observaciones = request.form.get('observaciones', '').strip()
            fecha_cierre = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # --- LÓGICA DE CIERRE: FILTRAR SOLO POR EFECTIVO FÍSICO (Para comparar con billetes) ---
            
            # 1. Calcular Sistema ARS (Solo lo que entró/salió en billetes de pesos)
            data_f_ars = db_query("""
                SELECT 
                    SUM(CASE WHEN tipo LIKE 'INGRESO%' THEN monto_ars ELSE 0 END) as ingresos,
                    SUM(CASE WHEN (tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%') THEN monto_ars ELSE 0 END) as egresos
                FROM caja_movimientos 
                WHERE metodo_pago = 'EFECTIVO' AND fecha BETWEEN ? AND ?
            """, (f_ap, fecha_cierre))[0]
            
            monto_sistema_ars = caja_abierta[0]['monto_inicial_ars'] + (data_f_ars['ingresos'] or 0.0) - (data_f_ars['egresos'] or 0.0)
            dif_ars = monto_contado_fisico_ars - monto_sistema_ars

            # 2. Calcular Sistema USD (Solo lo que entró/salió en billetes de dólares)
            data_f_usd = db_query("""
                SELECT 
                    SUM(CASE WHEN tipo LIKE 'INGRESO%' THEN monto_usd ELSE 0 END) as ingresos,
                    SUM(CASE WHEN (tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%') THEN monto_usd ELSE 0 END) as egresos
                FROM caja_movimientos 
                WHERE metodo_pago = 'EFECTIVO' AND fecha BETWEEN ? AND ?
            """, (f_ap, fecha_cierre))[0]
            
            monto_sistema_usd = caja_abierta[0]['monto_inicial_usd'] + (data_f_usd['ingresos'] or 0.0) - (data_f_usd['egresos'] or 0.0)
            dif_usd = monto_contado_fisico_usd - monto_sistema_usd

            db_execute("""
                UPDATE arqueo_caja SET fecha_cierre = ?, monto_sistema_calculado_ars = ?, monto_contado_fisico_ars = ?, diferencia_ars = ?, 
                monto_sistema_calculado_usd = ?, monto_contado_fisico_usd = ?, diferencia_usd = ?, observaciones = ?, estado = 'CERRADO' 
                WHERE id = ?
            """, (fecha_cierre, monto_sistema_ars, monto_contado_fisico_ars, dif_ars, monto_sistema_usd, monto_contado_fisico_usd, dif_usd, observaciones, arqueo_id))
            
            # Registros finales de cierre
            registrar_movimiento_caja(current_user.id, 'CIERRE_CAJA_ARS', monto_ars=monto_contado_fisico_ars, monto_usd=0, descripcion=f"Cierre - Dif: {dif_ars:.2f}", referencia_id=arqueo_id, metodo_pago='EFECTIVO')
            registrar_movimiento_caja(current_user.id, 'CIERRE_CAJA_USD', monto_ars=0, monto_usd=monto_contado_fisico_usd, descripcion=f"Cierre - Dif: {dif_usd:.2f}", referencia_id=arqueo_id, metodo_pago='EFECTIVO')
            
            flash(f"Caja física cerrada correctamente.", "success")
            return redirect(url_for('arqueo_caja'))

    # --- LÓGICA GET (DASHBOARD BIMONEDA Y MULTICUENTA) ---
    if caja_abierta:
        arqueo_actual = caja_abierta[0]
        f_ap = arqueo_actual['fecha_apertura']
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Función para obtener saldos limpios por cuenta (No mezcla USD con ARS)
        def get_clean_balances(cuenta):
            res = db_query("""
                SELECT 
                    SUM(CASE WHEN tipo LIKE 'INGRESO%' THEN monto_ars ELSE 0 END) -
                    SUM(CASE WHEN (tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%') THEN monto_ars ELSE 0 END) as ars,
                    SUM(CASE WHEN tipo LIKE 'INGRESO%' THEN monto_usd ELSE 0 END) -
                    SUM(CASE WHEN (tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%') THEN monto_usd ELSE 0 END) as usd
                FROM caja_movimientos 
                WHERE metodo_pago = ? AND fecha BETWEEN ? AND ?
            """, (cuenta, f_ap, ahora))[0]
            
            # Sumar monto inicial solo si es Efectivo
            inicial_ars = arqueo_actual['monto_inicial_ars'] if cuenta == 'EFECTIVO' else 0
            inicial_usd = arqueo_actual['monto_inicial_usd'] if cuenta == 'EFECTIVO' else 0
            
            return (inicial_ars + (res['ars'] or 0.0)), (inicial_usd + (res['usd'] or 0.0))

        # Recopilar datos para las tarjetas del Dashboard
        s_f_ars, s_f_usd = get_clean_balances('EFECTIVO')
        s_b_ars, s_b_usd = get_clean_balances('BANCO')
        s_m_ars, s_m_usd = get_clean_balances('MERCADO_PAGO')

        totales_visualizar = {
            'Efectivo Físico (ARS)': s_f_ars,
            'Efectivo Físico (USD)': s_f_usd,
            'Banco / Transf. (ARS)': s_b_ars,
            'Banco / Transf. (USD)': s_b_usd,
            'Mercado Pago (ARS)': s_m_ars,
            'Mercado Pago (USD)': s_m_usd
        }

        return render_template('caja/arqueo.html', 
                               arqueo_abierto=True, 
                               arqueo_actual=arqueo_actual, 
                               saldo_actual_sistema_ars=s_f_ars, # Usado para el formulario de cierre
                               saldo_actual_sistema_usd=s_f_usd, # Usado para el formulario de cierre
                               totales_visualizar=totales_visualizar)
    
    return render_template('caja/arqueo.html', arqueo_abierto=False)  
    
@app.route('/caja/arqueos_historial')
@login_required
@admin_required
def arqueos_historial():
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_usuario = request.args.get('usuario', '')
    filtro_estado = request.args.get('estado', '')

    query = """
        SELECT ac.id, ac.fecha_apertura, ac.fecha_cierre,
               COALESCE(ac.monto_inicial_ars, 0.0) AS monto_inicial_ars,
               COALESCE(ac.monto_inicial_usd, 0.0) AS monto_inicial_usd,
               COALESCE(ac.monto_sistema_calculado_ars, 0.0) AS monto_sistema_calculado_ars,
               COALESCE(ac.monto_contado_fisico_ars, 0.0) AS monto_contado_fisico_ars,
               COALESCE(ac.diferencia_ars, 0.0) AS diferencia_ars,
               COALESCE(ac.monto_sistema_calculado_usd, 0.0) AS monto_sistema_calculado_usd,
               COALESCE(ac.monto_contado_fisico_usd, 0.0) AS monto_contado_fisico_usd,
               COALESCE(ac.diferencia_usd, 0.0) AS diferencia_usd,
               ac.observaciones, ac.estado, u.username
        FROM arqueo_caja ac JOIN users u ON ac.user_id = u.id
        WHERE ac.fecha_apertura BETWEEN ? AND ?
    """
    params = [start_date, end_date_query]

    if filtro_usuario:
        query += " AND u.id = ?"
        params.append(filtro_usuario)
    if filtro_estado:
        query += " AND ac.estado = ?"
        params.append(filtro_estado)

    query += " ORDER BY ac.fecha_cierre DESC, ac.fecha_apertura DESC"
    arqueos = db_query(query, tuple(params))
    
    usuarios_disponibles = db_query("SELECT id, username FROM users ORDER BY username")

    return render_template('caja/arqueos_historial.html', arqueos=arqueos,
                           start_date=start_date, end_date=end_date_display,
                           filtros_activos={'usuario': filtro_usuario, 'estado': filtro_estado},
                           usuarios_disponibles=usuarios_disponibles)

# =================================================================
# === RUTAS DE REPORTES Y EXPORTACIÓN (Incluyendo nuevas plantillas) =============
# =================================================================
@app.route('/reportes')
@login_required
def reportes_menu():
    return render_template('reportes/menu.html')

# ... (otras importaciones y código) ...

# ... (otras importaciones y código) ...

@app.route('/reportes/actividad')
@login_required
def reporte_actividad():
    start_date, end_date_display, end_date_query = get_date_filters()
    params = (start_date, end_date_query)
    
    ventas_query = "SELECT COUNT(id) as total_ventas, COALESCE(SUM(precio_final_ars), 0.0) as facturacion_total FROM ventas WHERE status = 'COMPLETADA' AND fecha_venta BETWEEN ? AND ?"
    raw_metricas_ventas = db_query(ventas_query, params)
    
    # ## MODIFICACIÓN CLAVE ##: Convertir sqlite3.Row a un diccionario explícitamente
    metricas_ventas = dict(raw_metricas_ventas[0]) if raw_metricas_ventas else {}
    # Ahora .get() funcionará de forma segura porque metricas_ventas es siempre un dict
    metricas_ventas['facturacion_total'] = float(metricas_ventas.get('facturacion_total', 0.0))
    metricas_ventas['total_ventas'] = int(metricas_ventas.get('total_ventas', 0))

    reparaciones_query = "SELECT COUNT(id) as total_reparaciones, COALESCE(SUM(precio_final_ars), 0.0) as facturacion_total FROM servicios_reparacion WHERE status = 'COMPLETADO' AND fecha_servicio BETWEEN ? AND ?"
    raw_metricas_reparaciones = db_query(reparaciones_query, params)
    
    # ## MODIFICACIÓN CLAVE ##: Convertir sqlite3.Row a un diccionario explícitamente
    metricas_reparaciones = dict(raw_metricas_reparaciones[0]) if raw_metricas_reparaciones else {}
    # Ahora .get() funcionará de forma segura
    metricas_reparaciones['facturacion_total'] = float(metricas_reparaciones.get('facturacion_total', 0.0))
    metricas_reparaciones['total_reparaciones'] = int(metricas_reparaciones.get('total_reparaciones', 0))
    
    facturacion_unificada = (metricas_ventas['facturacion_total'] or 0.0) + (metricas_reparaciones['facturacion_total'] or 0.0)
    
    top_celulares = db_query("SELECT c.marca, c.modelo, c.condicion, COUNT(v.celular_id) as unidades FROM ventas v JOIN celulares c ON v.celular_id = c.id WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ? GROUP BY c.marca, c.modelo, c.condicion ORDER BY unidades DESC LIMIT 5", params)
    
    top_servicios = db_query("SELECT falla_reportada, COUNT(id) as cantidad FROM servicios_reparacion WHERE status = 'COMPLETADO' AND fecha_servicio BETWEEN ? AND ? GROUP BY falla_reportada ORDER BY cantidad DESC LIMIT 5", params)
    
    return render_template('reportes/actividad.html',
                           metricas_ventas=metricas_ventas,
                           metricas_reparaciones=metricas_reparaciones,
                           facturacion_unificada=facturacion_unificada,
                           top_celulares=top_celulares,
                           top_servicios=top_servicios,
                           start_date=start_date, end_date=end_date_display)

# ... (resto del código de app.py) ...

# ... (resto del código de app.py) ...

@app.route('/reportes/saldos_cuentas')
@login_required
@admin_required
def reporte_saldos_cuentas():
    # 1. Obtener cotización actual para el total general
    dolar_info = inject_dolar_values()
    valor_dolar = dolar_info['valor_dolar_venta'] or 1.0

    # 2. Obtener todas las cuentas activas y sus titulares desde la base de datos
    cuentas_data = db_query("SELECT nombre, titular FROM cuentas_entidades WHERE activo = 1")
    
    # Crear un mapeo de Nombre de Cuenta -> Titular para facilitar la asignación
    titulares_map = {c['nombre']: c['titular'] for c in cuentas_data}
    
    # REGLA SOLICITADA: Para el Arqueo de Caja (EFECTIVO), el titular debe ser 'My Point'
    titulares_map['EFECTIVO'] = 'My Point'

    # Aseguramos que 'EFECTIVO' esté en la lista de nombres, incluso si no está en la tabla de entidades
    lista_nombres_cuentas = list(titulares_map.keys())
    if 'EFECTIVO' not in lista_nombres_cuentas:
        lista_nombres_cuentas.append('EFECTIVO')

    saldos_detalle = []
    total_general_ars_equiv = 0
    total_general_usd_fisico = 0
    total_general_ars_fisico = 0

    for nombre in lista_nombres_cuentas:
        # Obtener el titular correspondiente (usa "Sin asignar" por defecto si no existe)
        titular_actual = titulares_map.get(nombre, "Sin asignar")

        # Calculamos ingresos y egresos para esta cuenta específica
        # Filtramos por tipo para saber qué suma y qué resta
        res = db_query("""
            SELECT 
                SUM(CASE 
                    WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' OR tipo LIKE '%VENTA%' OR tipo LIKE '%COBRO%' OR tipo LIKE '%SERVICIO%' 
                    THEN monto_ars ELSE 0 END) -
                SUM(CASE 
                    WHEN tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%' 
                    THEN monto_ars ELSE 0 END) as neto_ars,
                
                SUM(CASE 
                    WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' OR tipo LIKE '%VENTA%' OR tipo LIKE '%COBRO%' OR tipo LIKE '%SERVICIO%' 
                    THEN monto_usd ELSE 0 END) -
                SUM(CASE 
                    WHEN tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%' 
                    THEN monto_usd ELSE 0 END) as neto_usd
            FROM caja_movimientos 
            WHERE metodo_pago = ?
        """, (nombre,))[0]

        n_ars = res['neto_ars'] or 0.0
        n_usd = res['neto_usd'] or 0.0
        
        # Equivalente en pesos de esta cuenta
        equiv_ars = n_ars + (n_usd * valor_dolar)

        saldos_detalle.append({
            'cuenta': nombre,
            'titular': titular_actual, # <--- CAMBIO AGREGADO: Titular de la cuenta
            'saldo_ars': n_ars,
            'saldo_usd': n_usd,
            'total_equivalente': equiv_ars
        })

        total_general_ars_fisico += n_ars
        total_general_usd_fisico += n_usd
        total_general_ars_equiv += equiv_ars

    return render_template('reportes/saldos_cuentas.html',
                           saldos=saldos_detalle,
                           total_ars=total_general_ars_fisico,
                           total_usd=total_general_usd_fisico,
                           total_equiv=total_general_ars_equiv,
                           valor_dolar=valor_dolar)


# ... (código anterior) ...
@app.route('/reportes/pagos_tecnico')
@login_required
@admin_required
def reporte_pagos_tecnico():
    start_date, end_date_display, end_date_query = get_date_filters()
    nombre_tecnico = request.args.get('nombre_tecnico', '').strip()

    tecnicos_sugeridos = db_query("SELECT DISTINCT tecnico_nombre FROM servicios_reparacion WHERE tecnico_nombre IS NOT NULL ORDER BY tecnico_nombre ASC")

    # Solo traemos lo que NO está pagado y tiene Mano de Obra
    query = """
        SELECT s.id, s.fecha_servicio, p.nombre, p.apellido, p.razon_social, 
               s.falla_reportada, s.precio_mano_obra_ars, s.tecnico_nombre
        FROM servicios_reparacion s
        JOIN personas p ON s.cliente_id = p.id
        WHERE s.status = 'COMPLETADO' 
          AND s.precio_mano_obra_ars > 0
          AND (s.pago_tecnico_estado IS NULL OR s.pago_tecnico_estado != 'PAGADO')
    """
    params = []
    if nombre_tecnico:
        query += " AND s.tecnico_nombre = ?"
        params.append(nombre_tecnico)
    
    reparaciones_raw = db_query(query + " ORDER BY s.fecha_servicio ASC", tuple(params))
    
    reparaciones = []
    for r in reparaciones_raw:
        item = dict(r)
        # Por defecto sugerimos 50% en el objeto, pero el HTML lo dejará cambiar
        item['comision_pct'] = 50 
        item['comision_total_ars'] = (item['precio_mano_obra_ars'] * 0.5)
        item['saldo_pendiente_ars'] = item['comision_total_ars']
        reparaciones.append(item)

    return render_template('reportes/pagos_tecnico.html',
                           reparaciones=reparaciones,
                           nombre_tecnico=nombre_tecnico,
                           tecnicos_sugeridos=tecnicos_sugeridos,
                           start_date=start_date,
                           end_date=end_date_display)
    
    
@app.route('/servicio_tecnico/pagar_comision_final', methods=['POST'])
@login_required
@admin_required
def pagar_comision_final():
    db_conn = get_db()
    try:
        # 1. Obtenemos la lista de IDs seleccionados en los checkboxes
        ids = request.form.getlist('reparacion_ids[]')
        tecnico = request.form.get('tecnico_nombre') # Capturado del hidden input
        metodo_pago = request.form.get('metodo_pago', 'EFECTIVO') # Cuenta de salida
        
        if not ids:
            flash("No se seleccionaron reparaciones.", "warning")
            return redirect(url_for('reporte_pagos_tecnico'))

        db_conn.execute("BEGIN TRANSACTION")
        total_a_liquidar = 0

        # 2. RECORREMOS CADA ID SELECCIONADO (Aquí es donde va el código que preguntaste)
        for rid in ids:
            # Buscamos el % y el Monto calculado que envió el HTML para este ID específico
            pct = float(request.form.get(f'pct_input_{rid}', 50))
            monto_final_ars = float(request.form.get(f'monto_input_{rid}', 0))
            
            total_a_liquidar += monto_final_ars

            # AQUÍ VA EL BLOQUE DE ACTUALIZACIÓN:
            db_execute_func(db_conn, """
                UPDATE servicios_reparacion 
                SET comision_pct = ?, 
                    comision_pagada_ars = ?, 
                    pago_tecnico_estado = 'PAGADO', 
                    fecha_pago_tecnico = ?
                WHERE id = ?
            """, (pct, monto_final_ars, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rid))

        # 3. Registramos el movimiento de salida de dinero de la CAJA (un solo asiento por el total)
        if total_a_liquidar > 0:
            registrar_movimiento_caja(
                user_id=current_user.id,
                tipo='EGRESO_PAGO_TECNICO_ARS',
                monto_ars=total_a_liquidar,
                descripcion=f"Pago comisiones técnica a: {tecnico}. Tickets: {', '.join(ids)}",
                metodo_pago=metodo_pago,
                sub_categoria='Sueldos'
            )

        db_conn.commit()
        flash(f"Liquidación exitosa. Se pagaron ${total_a_liquidar:,.2f} al técnico {tecnico}.", "success")
        
    except Exception as e:
        db_conn.rollback()
        app.logger.error(f"Error en liquidacion de comisiones: {e}")
        flash(f"Error al procesar el pago: {e}", "danger")
        
    return redirect(url_for('reporte_pagos_tecnico', nombre_tecnico=tecnico))


@app.route('/reportes/rentabilidad')
@login_required
@admin_required
def reporte_rentabilidad():
    start_date, end_date_display, end_date_query = get_date_filters()
    dolar_info = inject_dolar_values()
    dolar_c = dolar_info['valor_dolar_compra'] or 1.0

    # --- 1. RESUMEN EQUIPOS POR TIPO ---
    resumen_equipos = db_query("""
        SELECT 
            COALESCE(comp.tipo_item, 'CELULAR') as clase,
            COALESCE(SUM(v.precio_final_ars), 0) as total_venta, 
            COALESCE(SUM(c.costo_usd * v.valor_dolar_momento), 0) as total_costo
        FROM ventas v 
        JOIN celulares c ON v.celular_id = c.id
        LEFT JOIN compras comp ON c.id = comp.item_id AND comp.tipo_item IN ('CELULAR', 'TABLET', 'SMARTWATCH', 'EQUIPO')
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
        GROUP BY 1
    """, (start_date, end_date_query))

    # --- 2. RESUMEN INSUMOS ---
    resumen_insumos = db_query("""
        SELECT COALESCE(r.categoria, 'MANUAL') as clase,
               COALESCE(SUM(ru.cantidad * ru.costo_usd_momento * ?), 0) as total_venta,
               COALESCE(SUM(ru.cantidad * r.costo_usd * ?), 0) as total_costo
        FROM repuestos_usados ru
        JOIN servicios_reparacion sr ON ru.servicio_id = sr.id
        LEFT JOIN repuestos r ON ru.repuesto_id = r.id
        WHERE sr.status = 'COMPLETADO' AND sr.fecha_servicio BETWEEN ? AND ?
        GROUP BY 1
    """, (dolar_c, dolar_c, start_date, end_date_query))

    # --- 3. MANO DE OBRA VS COMISIONES ---
    res_mo = db_query("""
        SELECT 
            COALESCE(SUM(precio_mano_obra_ars), 0) as total_bruto,
            COALESCE(SUM(comision_pagada_ars), 0) as total_comisiones
        FROM servicios_reparacion 
        WHERE status = 'COMPLETADO' AND fecha_servicio BETWEEN ? AND ?
    """, (start_date, end_date_query))[0]
    
    mano_obra_data = {
        'bruto': res_mo['total_bruto'],
        'comisiones': res_mo['total_comisiones'],
        'neto': res_mo['total_bruto'] - res_mo['total_comisiones']
    }

    # --- 4. GASTOS OPERATIVOS CAJA (MODIFICADO: EXCLUYE SOCIOS Y SOLO EFECTIVO) ---
    resumen_gastos = db_query("""
        SELECT COALESCE(sub_categoria, 'OTROS') as clase,
               COALESCE(SUM(monto_ars + (monto_usd * ?)), 0) as total
        FROM caja_movimientos
        WHERE (tipo LIKE 'EGRESO_MANUAL%' OR tipo LIKE 'PAGO_TECNICO%')
          AND metodo_pago = 'EFECTIVO'
          AND (sub_categoria IS NULL OR sub_categoria NOT IN ('Aportes Socios', 'Retiros Socios'))
          AND fecha BETWEEN ? AND ?
        GROUP BY sub_categoria
    """, (dolar_c, start_date, end_date_query))

    # --- 4b. EGRESOS VIRTUALES MANUALES (NUEVO: SOLO EGRESO_MANUAL_VIRTUAL) ---
    egr_virtuales_man = db_query("""
        SELECT metodo_pago as cuenta,
               COALESCE(SUM(monto_ars + (monto_usd * ?)), 0) as total
        FROM caja_movimientos
        WHERE tipo = 'EGRESO_VIRTUAL'
          AND metodo_pago != 'EFECTIVO'
          AND fecha BETWEEN ? AND ?
        GROUP BY metodo_pago
    """, (dolar_c, start_date, end_date_query))

    # --- 5. FLUJO DE CUENTAS ---
    cuentas = db_query("SELECT nombre FROM cuentas_entidades WHERE activo = 1")
    nombres_cuentas = [c['nombre'] for c in cuentas]
    if 'EFECTIVO' not in nombres_cuentas: nombres_cuentas.append('EFECTIVO')

    flujo_cuentas = []
    for nc in nombres_cuentas:
        res = db_query("""
            SELECT 
                COALESCE(SUM(CASE WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' THEN monto_ars ELSE 0 END), 0) as ing_ars,
                COALESCE(SUM(CASE WHEN (tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%') THEN monto_ars ELSE 0 END), 0) as egr_ars,
                COALESCE(SUM(CASE WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' THEN monto_usd ELSE 0 END), 0) as ing_usd,
                COALESCE(SUM(CASE WHEN (tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%') THEN monto_usd ELSE 0 END), 0) as egr_usd
            FROM caja_movimientos 
            WHERE metodo_pago = ? AND fecha BETWEEN ? AND ?
        """, (nc, start_date, end_date_query))[0]
        flujo_cuentas.append({
            'nombre': nc,
            'ing_ars': res['ing_ars'], 'egr_ars': res['egr_ars'],
            'ing_usd': res['ing_usd'], 'egr_usd': res['egr_usd']
        })

    # Detalle de Ventas
    ventas_detalle = db_query("""
        SELECT v.fecha_venta, c.marca, c.modelo, c.imei, v.precio_final_ars, 
               COALESCE(c.costo_usd * v.valor_dolar_momento, 0) as costo_ars
        FROM ventas v JOIN celulares c ON v.celular_id = c.id 
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
    """, (start_date, end_date_query))

    # Detalle de Insumos
    insumos_detalle = db_query("""
        SELECT sr.fecha_servicio, COALESCE(r.nombre_parte, ru.manual_item_nombre) as nombre_parte, 
               COALESCE(r.categoria, 'MANUAL') as categoria, ru.cantidad,
               COALESCE(ru.costo_usd_momento * ?, 0) as precio_venta_ars,
               COALESCE(r.costo_usd * ?, 0) as costo_compra_ars
        FROM repuestos_usados ru
        JOIN servicios_reparacion sr ON ru.servicio_id = sr.id
        LEFT JOIN repuestos r ON ru.repuesto_id = r.id
        WHERE sr.status = 'COMPLETADO' AND sr.fecha_servicio BETWEEN ? AND ?
    """, (dolar_c, dolar_c, start_date, end_date_query))

    return render_template('reportes/rentabilidad.html',
                           resumen_equipos=resumen_equipos,
                           resumen_insumos=resumen_insumos,
                           mano_obra=mano_obra_data,
                           resumen_gastos=resumen_gastos,
                           egr_virtuales_man=egr_virtuales_man,
                           flujo_cuentas=flujo_cuentas,
                           ventas_detalle=ventas_detalle,
                           insumos_detalle=insumos_detalle,
                           start_date=start_date, end_date=end_date_display)
    

@app.route('/reportes/detalle_ventas_rentabilidad')
@login_required
@admin_required
def detalle_ventas_rentabilidad():
    start_date, end_date_display, end_date_query = get_date_filters()
    
    # Consulta detallada en DÓLARES
    ventas = db_query("""
        SELECT v.id, v.fecha_venta, c.marca, c.modelo, c.imei, 
               v.precio_final_usd, 
               COALESCE(c.costo_usd, 0) as costo_usd,
               (v.precio_final_usd - COALESCE(c.costo_usd, 0)) as margen_usd
        FROM ventas v 
        JOIN celulares c ON v.celular_id = c.id 
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
        ORDER BY v.fecha_venta DESC
    """, (start_date, end_date_query))

    return render_template('reportes/detalle_ventas.html', 
                           ventas=ventas, start_date=start_date, end_date=end_date_display)

@app.route('/exportar/detalle_ventas_usd')
@login_required
@admin_required
def exportar_detalle_ventas_usd():
    start_date, _, end_date_query = get_date_filters()
    
    ventas = db_query("""
        SELECT v.fecha_venta, v.id, c.marca, c.modelo, c.imei, 
               v.precio_final_usd, c.costo_usd,
               (v.precio_final_usd - c.costo_usd) as margen_usd
        FROM ventas v 
        JOIN celulares c ON v.celular_id = c.id 
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
        ORDER BY v.fecha_venta DESC
    """, (start_date, end_date_query))

    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel
    writer = csv.writer(output, delimiter=';')
    
    writer.writerow(['Fecha', 'ID Venta', 'Marca', 'Modelo', 'IMEI', 'Costo (USD)', 'Venta (USD)', 'Margen (USD)'])
    
    for v in ventas:
        writer.writerow([
            v['fecha_venta'], v['id'], v['marca'], v['modelo'], v['imei'],
            f"{v['costo_usd']:.2f}".replace('.', ','),
            f"{v['precio_final_usd']:.2f}".replace('.', ','),
            f"{v['margen_usd']:.2f}".replace('.', ',')
        ])
    
    output.seek(0)
    filename = f"Detalle_Ventas_USD_{start_date}.csv"
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename={filename}"})


@app.route('/exportar/detalle_servicios_ars')
@login_required
@admin_required
def exportar_detalle_servicios_ars():
    start_date, _, end_date_query = get_date_filters()
    dolar_info = inject_dolar_values()
    dolar_c = dolar_info['valor_dolar_compra'] or 1.0

    # Obtenemos los mismos datos que el reporte visual
    servicios = db_query("""
        SELECT sr.id, sr.fecha_servicio, 
               COALESCE(p.razon_social, p.nombre || ' ' || p.apellido) as cliente,
               sr.tecnico_nombre, sr.precio_mano_obra_ars, sr.precio_final_ars,
               COALESCE((SELECT SUM(ru.cantidad * r.costo_usd * ?) 
                         FROM repuestos_usados ru 
                         LEFT JOIN repuestos r ON ru.repuesto_id = r.id 
                         WHERE ru.servicio_id = sr.id), 0) as costo_insumos_ars
        FROM servicios_reparacion sr
        JOIN personas p ON sr.cliente_id = p.id
        WHERE sr.status = 'COMPLETADO' AND sr.fecha_servicio BETWEEN ? AND ?
        ORDER BY sr.fecha_servicio DESC
    """, (dolar_c, start_date, end_date_query))

    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel
    writer = csv.writer(output, delimiter=';')
    
    # Encabezados
    writer.writerow(['Fecha', 'ID Servicio', 'Cliente', 'Técnico', 'Mano de Obra (ARS)', 'Costo Repuestos (ARS)', 'Precio Final (ARS)', 'Utilidad Bruta (ARS)'])
    
    for s in servicios:
        utilidad = (s['precio_final_ars'] or 0) - (s['costo_insumos_ars'] or 0)
        writer.writerow([
            s['fecha_servicio'], 
            s['id'], 
            s['cliente'], 
            s['tecnico_nombre'],
            f"{s['precio_mano_obra_ars']:.2f}".replace('.', ','),
            f"{s['costo_insumos_ars']:.2f}".replace('.', ','),
            f"{s['precio_final_ars']:.2f}".replace('.', ','),
            f"{utilidad:.2f}".replace('.', ',')
        ])
    
    output.seek(0)
    filename = f"Detalle_Servicios_Rentabilidad_{start_date}.csv"
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": f"attachment;filename={filename}"})


@app.route('/reportes/detalle_servicios_rentabilidad')
@login_required
@admin_required
def detalle_servicios_rentabilidad():
    start_date, end_date_display, end_date_query = get_date_filters()
    dolar_info = inject_dolar_values()
    dolar_c = dolar_info['valor_dolar_compra'] or 1.0

    # Consulta de servicios con desglose de MO y Repuestos
    servicios = db_query("""
        SELECT sr.id, sr.fecha_servicio, p.razon_social, p.nombre, p.apellido,
               sr.tecnico_nombre, sr.precio_mano_obra_ars, sr.precio_final_ars,
               COALESCE((SELECT SUM(ru.cantidad * r.costo_usd * ?) 
                         FROM repuestos_usados ru 
                         LEFT JOIN repuestos r ON ru.repuesto_id = r.id 
                         WHERE ru.servicio_id = sr.id), 0) as costo_insumos_ars
        FROM servicios_reparacion sr
        JOIN personas p ON sr.cliente_id = p.id
        WHERE sr.status = 'COMPLETADO' AND sr.fecha_servicio BETWEEN ? AND ?
        ORDER BY sr.fecha_servicio DESC
    """, (dolar_c, start_date, end_date_query))

    return render_template('reportes/detalle_servicios.html', 
                           servicios=servicios, start_date=start_date, end_date=end_date_display)
    
# ... (resto del código) ...
# ... (resto del código) ...
# ... (resto del código) ...
@app.route('/reportes/inventario')
@login_required
@admin_required
def reporte_inventario():
    # Dolar info ya viene del context processor
    dolar_info_from_context = inject_dolar_values()
    valor_dolar_compra_local = dolar_info_from_context['valor_dolar_compra'] 
    valor_dolar_venta_local = dolar_info_from_context['valor_dolar_venta']
    
    if valor_dolar_compra_local is None or valor_dolar_compra_local == 0:
        flash("Advertencia: No se pudo obtener la cotización real del dólar de COMPRA. Se usará 1.0 para valoraciones.", "warning")
        valor_dolar_compra_local = 1.0
    if valor_dolar_venta_local is None or valor_dolar_venta_local == 0:
        flash("Advertencia: No se pudo obtener la cotización real del dólar de VENTA. Se usará 1.0 para valoraciones.", "warning")
        valor_dolar_venta_local = 1.0

    stock_bajo_limite = request.args.get('stock_bajo_limite', 5, type=int)

    # Celulares: Ahora cada item es una unidad, stock es 1 o 0
    valoracion_celulares = db_query("SELECT COUNT(id) as total_unidades, COALESCE(SUM(costo_usd), 0) as costo_total_usd FROM celulares WHERE stock > 0")[0]
    stock_bajo_celulares = db_query("SELECT * FROM celulares WHERE stock > 0 ORDER BY marca, modelo ASC") # Celulares no tienen un "stock bajo" inherente como repuestos
    
    # MODIFICADO: Separar celulares sin stock que son parte de pago de los que simplemente se vendieron
    celulares_sin_stock_vendidos = db_query("SELECT * FROM celulares WHERE stock = 0 AND es_parte_pago = 0 ORDER BY marca, modelo ASC")
    celulares_parte_pago_recibidos = db_query("SELECT * FROM celulares WHERE stock = 1 AND es_parte_pago = 1 ORDER BY marca, modelo ASC")
    celulares_parte_pago_en_proceso = db_query("SELECT * FROM celulares WHERE stock = 0 AND es_parte_pago = 1 ORDER BY marca, modelo ASC")

    # Repuestos
    # MODIFICACIÓN: La valoración de repuestos en inventario debe ser a costo para informes internos.
    valoracion_repuestos = db_query("SELECT COALESCE(SUM(stock), 0) as total_unidades, COALESCE(SUM(stock * costo_usd), 0) as costo_total_usd FROM repuestos WHERE stock > 0")[0]
    stock_bajo_repuestos = db_query("SELECT * FROM repuestos WHERE stock > 0 AND stock <= ? ORDER BY stock ASC", (stock_bajo_limite,))
    repuestos_sin_stock = db_query("SELECT * FROM repuestos WHERE stock = 0 ORDER BY nombre_parte ASC")
    
    costo_total_unificado_usd = (valoracion_celulares['costo_total_usd'] or 0) + (valoracion_repuestos['costo_total_usd'] or 0)
    
    return render_template('reportes/inventario.html', 
                           valoracion_celulares=valoracion_celulares,
                           stock_bajo_celulares=stock_bajo_celulares,
                           celulares_sin_stock_vendidos=celulares_sin_stock_vendidos, # Nuevo
                           celulares_parte_pago_recibidos=celulares_parte_pago_recibidos, # Nuevo
                           celulares_parte_pago_en_proceso=celulares_parte_pago_en_proceso, # Nuevo
                           valoracion_repuestos=valoracion_repuestos,
                           stock_bajo_repuestos=stock_bajo_repuestos,
                           repuestos_sin_stock=repuestos_sin_stock, 
                           costo_total_unificado_usd=costo_total_unificado_usd,
                           valor_dolar_compra=valor_dolar_compra_local, # Se pasa directamente valor_dolar_compra
                           valor_dolar_venta=valor_dolar_venta_local,   # Se pasa directamente valor_dolar_venta
                           stock_bajo_limite=stock_bajo_limite)

@app.route('/reportes/estacionalidad')
@login_required
def reporte_estacionalidad():
    start_date, end_date_display, end_date_query = get_date_filters()
    
    ventas_mes_data = db_query("SELECT strftime('%Y-%m', fecha_venta) as mes, COUNT(id) as cantidad FROM ventas WHERE status = 'COMPLETADA' GROUP BY mes ORDER BY mes")
    ventas_dia_data = db_query("SELECT CASE strftime('%w', fecha_venta) WHEN '0' THEN 'Domingo' WHEN '1' THEN 'Lunes' WHEN '2' THEN 'Martes' WHEN '3' THEN 'Miércoles' WHEN '4' THEN 'Jueves' WHEN '5' THEN 'Viernes' ELSE 'Sábado' END as dia, COUNT(id) as cantidad FROM ventas WHERE status = 'COMPLETADA' GROUP BY dia ORDER BY strftime('%w', fecha_venta)")
    reparaciones_mes_data = db_query("SELECT strftime('%Y-%m', fecha_servicio) as mes, COUNT(id) as cantidad FROM servicios_reparacion WHERE status = 'COMPLETADO' GROUP BY mes ORDER BY mes")
    reparaciones_dia_data = db_query("SELECT CASE strftime('%w', fecha_servicio) WHEN '0' THEN 'Domingo' WHEN '1' THEN 'Lunes' WHEN '2' THEN 'Martes' WHEN '3' THEN 'Miércoles' WHEN '4' THEN 'Jueves' WHEN '5' THEN 'Viernes' ELSE 'Sábado' END as dia, COUNT(id) as cantidad FROM servicios_reparacion WHERE status = 'COMPLETADO' GROUP BY dia ORDER BY strftime('%w', fecha_servicio)")
    return render_template('reportes/estacionalidad.html', 
                           ventas_mes_labels=json.dumps([d['mes'] for d in ventas_mes_data]),
                           ventas_mes_values=json.dumps([d['cantidad'] for d in ventas_mes_data]),
                           ventas_dia_labels=json.dumps([d['dia'] for d in ventas_dia_data]),
                           ventas_dia_values=json.dumps([d['cantidad'] for d in ventas_dia_data]),
                           reparaciones_mes_labels=json.dumps([d['mes'] for d in reparaciones_mes_data]),
                           reparaciones_mes_values=json.dumps([d['cantidad'] for d in reparaciones_mes_data]),
                           reparaciones_dia_labels=json.dumps([d['dia'] for d in reparaciones_dia_data]),
                           reparaciones_dia_values=json.dumps([d['cantidad'] for d in reparaciones_dia_data]))

# ... (código anterior de app.py) ...

# ... (resto de tu app.py, incluyendo imports y otras funciones) ...

# ... (resto de tu app.py, incluyendo imports y otras funciones) ...

@app.route('/reportes/auditoria')
@login_required
@admin_required
def reporte_auditoria():
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_usuario = request.args.get('usuario', '')
    filtro_movimiento = request.args.get('movimiento', '').strip()
    filtro_item_tipo = request.args.get('item_tipo', '').strip()
    filtro_detalles = request.args.get('detalles', '').strip()

    # --- Construcción de las subconsultas para UNION ALL ---
    query_parts = []
    # Los parámetros para las fechas se añaden una vez por cada UNION ALL
    date_params_for_union = [start_date, end_date_query]

    # Subconsulta para movimientos de la tabla 'movimientos'
    query_parts.append("""
        SELECT
            m.id,
            m.fecha,
            u.username,
            m.user_id,
            m.tipo_movimiento,
            m.tipo_item,
            m.detalles,
            NULL AS sub_categoria,
            'general' AS source_table
        FROM movimientos m
        JOIN users u ON m.user_id = u.id
        WHERE m.fecha BETWEEN ? AND ?
    """)

    # Subconsulta para movimientos de la tabla 'caja_movimientos'
    query_parts.append("""
        SELECT
            cm.id,
            cm.fecha,
            u.username,
            cm.user_id,
            cm.tipo AS tipo_movimiento,
            'CAJA' AS tipo_item,
            CASE
                WHEN cm.sub_categoria IS NOT NULL AND cm.sub_categoria != '' THEN JSON_OBJECT('Descripción', cm.descripcion, 'Sub-Rubro', cm.sub_categoria)
                ELSE JSON_OBJECT('Descripción', cm.descripcion)
            END AS detalles,
            cm.sub_categoria,
            'caja' AS source_table
        FROM caja_movimientos cm
        JOIN users u ON cm.user_id = u.id
        WHERE cm.fecha BETWEEN ? AND ?
    """)

    # Unimos ambas subconsultas con UNION ALL
    full_query_base = " UNION ALL ".join(query_parts)
    
    # Concatenamos los parámetros de fecha para el UNION ALL
    # Si hay N subconsultas en UNION ALL, los parámetros de fecha se repiten N veces
    final_params = date_params_for_union * len(query_parts)


    # --- Aplicación de filtros dinámicos a la consulta consolidada ---
    where_clauses_dynamic = []
    filter_params_dynamic = []

    if filtro_usuario:
        where_clauses_dynamic.append("user_id = ?")
        filter_params_dynamic.append(filtro_usuario)
    
    if filtro_movimiento:
        where_clauses_dynamic.append("tipo_movimiento LIKE ?")
        filter_params_dynamic.append(f"%{filtro_movimiento}%")

    if filtro_item_tipo:
        where_clauses_dynamic.append("tipo_item = ?")
        filter_params_dynamic.append(filtro_item_tipo)

    if filtro_detalles:
        # CORRECCIÓN: Aquí estaba el error tipográfico (filro_detalles -> filtro_detalles)
        where_clauses_dynamic.append("detalles LIKE ?")
        filter_params_dynamic.append(f"%{filtro_detalles}%")

    # Construimos la consulta final
    if where_clauses_dynamic:
        final_query = f"SELECT id, fecha, username, tipo_movimiento, tipo_item, detalles, sub_categoria, source_table FROM ({full_query_base}) AS combined_data WHERE {' AND '.join(where_clauses_dynamic)}"
        final_params.extend(filter_params_dynamic) # Añadir los parámetros dinámicos
    else:
        final_query = f"SELECT id, fecha, username, tipo_movimiento, tipo_item, detalles, sub_categoria, source_table FROM ({full_query_base}) AS combined_data"

    final_query += " ORDER BY fecha DESC"
    
    raw_movimientos = db_query(final_query, tuple(final_params))
    
    movimientos = []
    for mov_row in raw_movimientos:
        mov = dict(mov_row) # Convertir sqlite3.Row a un diccionario mutable
        if mov['detalles']:
            try:
                # Intentamos parsear el JSON. Si falla, asumimos que es una cadena simple.
                mov['detalles_parsed'] = json.loads(mov['detalles'])
            except (json.JSONDecodeError, TypeError): # Añadido TypeError para manejar casos donde 'detalles' no es string
                mov['detalles_parsed'] = {'Descripción': str(mov['detalles'])}
        else:
            mov['detalles_parsed'] = {}
            
        movimientos.append(mov)

    # Obtenemos los usuarios para el filtro de la plantilla
    usuarios = db_query("SELECT id, username FROM users ORDER BY username")
    
    # Consolidamos los tipos de movimiento para el filtro, incluyendo los de caja (normalizados)
    mov_types_general = db_query("SELECT DISTINCT tipo_movimiento FROM movimientos")
    mov_types_caja = db_query("SELECT DISTINCT tipo FROM caja_movimientos")

    all_mov_types_set = set()
    for m in mov_types_general:
        all_mov_types_set.add(m['tipo_movimiento'])
    for m in mov_types_caja:
        raw_type = m['tipo']
        # Normalizar tipos de movimiento de caja (ej. INGRESO_MANUAL_ARS -> INGRESO_MANUAL)
        if raw_type.endswith('_ARS') or raw_type.endswith('_USD'):
            base_type = '_'.join(raw_type.split('_')[:-1]) 
            all_mov_types_set.add(base_type)
        else:
            all_mov_types_set.add(raw_type)

    tipos_movimiento_all = sorted(list(all_mov_types_set))

    # Consolidamos los tipos de ítem para el filtro
    item_types_general = db_query("SELECT DISTINCT tipo_item FROM movimientos WHERE tipo_item IS NOT NULL")
    all_item_types_set = set()
    for item in item_types_general:
        all_item_types_set.add(item['tipo_item'])
    all_item_types_set.add('CAJA') # Añadir 'CAJA' como tipo de ítem para los movimientos de caja

    tipos_item_all = sorted(list(all_item_types_set))

    return render_template('reportes/auditoria.html', 
                           movimientos=movimientos,
                           usuarios=usuarios, 
                           tipos_movimiento=tipos_movimiento_all,
                           tipos_item=tipos_item_all,
                           start_date=start_date, 
                           end_date=end_date_display, 
                           filtros_activos={'usuario': filtro_usuario, 'movimiento': filtro_movimiento, 'item_tipo': filtro_item_tipo, 'detalles': filtro_detalles})

# ... (resto del código de tu app.py) ...
# ... (resto del código de tu app.py) ...
# ... (resto del código de app.py) ...
@app.route('/reportes/libro_diario')
@login_required
def libro_diario():
    start_date, end_date_display, end_date_query = get_date_filters()
    
    # --- NUEVOS FILTROS ---
    filtro_forma_pago = request.args.get('forma_pago', '')
    filtro_origen = request.args.get('origen', '') # 'PROVEEDOR' o 'CLIENTE'

    # Dolar info (para visualización si se requiere, aunque el reporte ya tiene los montos calculados)
    dolar_info_from_context = inject_dolar_values()
    valor_dolar_compra_local = dolar_info_from_context['valor_dolar_compra'] 
    valor_dolar_venta_local = dolar_info_from_context['valor_dolar_venta'] 

    params = []

    # Construcción de la consulta unificada
    query = """
    SELECT * FROM (
        -- 1. Movimientos generales del sistema (Auditoría)
        SELECT 
            m.fecha, 
            u.username, 
            m.tipo_movimiento AS tipo_transaccion, 
            m.tipo_item AS categoria, 
            m.detalles,
            NULL AS monto_ingreso_ars,
            NULL AS monto_egreso_ars,
            NULL AS monto_ingreso_usd,
            NULL AS monto_egreso_usd,
            NULL AS sub_categoria,
            'OTRO' AS metodo_pago_filtro,
            'SISTEMA' AS origen_filtro
        FROM movimientos m
        JOIN users u ON m.user_id = u.id
        WHERE m.fecha BETWEEN ? AND ?

        UNION ALL

        -- 2. Movimientos de caja
        SELECT 
            cm.fecha, 
            u.username, 
            cm.tipo AS tipo_transaccion, 
            'CAJA' AS categoria, 
            cm.descripcion AS detalles,
            -- Lógica de Ingresos
            CASE WHEN cm.monto_ars > 0 AND (cm.tipo LIKE 'INGRESO%' OR cm.tipo = 'APERTURA_CAJA_ARS') THEN cm.monto_ars ELSE NULL END,
            -- Lógica de Egresos
            CASE WHEN cm.monto_ars > 0 AND (cm.tipo LIKE 'EGRESO%' OR cm.tipo = 'PAGO_PROVEEDOR_ARS' OR cm.tipo = 'CIERRE_CAJA_ARS') THEN cm.monto_ars ELSE NULL END,
            -- Lógica de Ingresos USD
            CASE WHEN cm.monto_usd > 0 AND (cm.tipo LIKE 'INGRESO%' OR cm.tipo = 'APERTURA_CAJA_USD') THEN cm.monto_usd ELSE NULL END,
            -- Lógica de Egresos USD
            CASE WHEN cm.monto_usd > 0 AND (cm.tipo LIKE 'EGRESO%' OR cm.tipo = 'PAGO_PROVEEDOR_USD' OR cm.tipo = 'CIERRE_CAJA_USD') THEN cm.monto_usd ELSE NULL END,
            
            cm.sub_categoria,
            COALESCE(cm.metodo_pago, 'EFECTIVO') AS metodo_pago_filtro,
            
            -- Clasificación de Origen para filtros
            CASE 
                WHEN cm.tipo LIKE '%PROVEEDOR%' THEN 'PROVEEDOR'
                WHEN cm.tipo LIKE '%VENTA%' OR cm.tipo LIKE '%COBRO%' OR cm.tipo LIKE '%SERVICIO%' THEN 'CLIENTE'
                ELSE 'CAJA_INTERNA' 
            END AS origen_filtro
        FROM caja_movimientos cm
        JOIN users u ON cm.user_id = u.id
        WHERE cm.fecha BETWEEN ? AND ?

        UNION ALL
        
        -- 3. Pagos a Proveedores (Cta Cte / Bancos)
        -- Filtramos EFECTIVO para no duplicar con caja_movimientos si se registraron ahí
        SELECT
            pp.fecha_pago AS fecha,
            u.username,
            'PAGO_PROVEEDOR' AS tipo_transaccion,
            'CUENTA_CORRIENTE' AS categoria,
            'Pago a Prov. ' || COALESCE(p.razon_social, p.nombre || ' ' || p.apellido) || ' (' || pp.observaciones || ')' AS detalles,
            NULL AS monto_ingreso_ars,
            pp.monto_ars AS monto_egreso_ars,
            NULL AS monto_ingreso_usd,
            pp.monto_usd AS monto_egreso_usd,
            'Pago Proveedores' AS sub_categoria,
            pp.tipo_pago AS metodo_pago_filtro,
            'PROVEEDOR' AS origen_filtro
        FROM pagos_proveedores pp
        JOIN users u ON pp.user_id = u.id
        JOIN personas p ON pp.proveedor_id = p.id
        WHERE pp.fecha_pago BETWEEN ? AND ? AND pp.tipo_pago NOT IN ('EFECTIVO_ARS', 'EFECTIVO_USD', 'EFECTIVO_ARS_USD_COMBINADO')

        UNION ALL

        -- 4. Cobros a Clientes (Deudas / Cta Cte / Bancos)
        -- Filtramos EFECTIVO si se registró en caja_movimientos (depende de tu implementación en cobrar_cliente)
        -- Asumiremos que EFECTIVO va a caja y aquí traemos el resto, o traemos todo si no duplica.
        -- En cobrar_cliente registraste en caja si era efectivo.
        SELECT
            cc.fecha_cobro AS fecha,
            u.username,
            'COBRO_CLIENTE' AS tipo_transaccion,
            'CUENTA_CORRIENTE' AS categoria,
            'Cobro a Cliente ' || COALESCE(p.razon_social, p.nombre || ' ' || p.apellido) || ' (' || cc.observaciones || ')' AS detalles,
            cc.monto_ars AS monto_ingreso_ars,
            NULL AS monto_egreso_ars,
            cc.monto_usd AS monto_ingreso_usd,
            NULL AS monto_egreso_usd,
            'Cobro Clientes' AS sub_categoria,
            cc.metodo_pago AS metodo_pago_filtro,
            'CLIENTE' AS origen_filtro
        FROM cobros_clientes cc
        JOIN users u ON cc.user_id = u.id
        JOIN personas p ON cc.cliente_id = p.id
        WHERE cc.fecha_cobro BETWEEN ? AND ? AND cc.metodo_pago NOT IN ('EFECTIVO', 'EFECTIVO_ARS', 'EFECTIVO_USD')

    ) AS unificado
    WHERE 1=1
    """
    
    # Añadir parámetros de fecha para los 4 SELECTs
    params.extend([start_date, end_date_query] * 4)

    # --- APLICAR FILTROS DINÁMICOS ---
    if filtro_forma_pago:
        query += " AND unificado.metodo_pago_filtro LIKE ?"
        params.append(f"%{filtro_forma_pago}%")
    
    if filtro_origen:
        if filtro_origen == 'PROVEEDOR':
            query += " AND unificado.origen_filtro = 'PROVEEDOR'"
        elif filtro_origen == 'CLIENTE':
            query += " AND unificado.origen_filtro = 'CLIENTE'"

    query += " ORDER BY fecha DESC"
    
    raw_movimientos_diarios = db_query(query, tuple(params))
    
    movimientos_diarios = []
    for mov_row in raw_movimientos_diarios:
        mov = dict(mov_row)
        
        # Parseo de detalles JSON
        if mov['detalles'] and '{' in str(mov['detalles']):
            try:
                mov['detalles_parsed'] = json.loads(mov['detalles'])
            except json.JSONDecodeError:
                mov['detalles_parsed'] = {'Descripción': mov['detalles']}
        else:
            mov['detalles_parsed'] = {'Descripción': mov['detalles']} if mov['detalles'] else {}

        # Formateo visual de tipos de transacción de caja
        if mov['categoria'] == 'CAJA':
            if mov['sub_categoria']:
                mov['detalles_parsed']['Sub-Rubro'] = mov['sub_categoria']
            # Mapeo de nombres técnicos a legibles
            mapping = {
                'INGRESO_VENTA': 'Venta Celular (Efectivo)',
                'INGRESO_SERVICIO_REPARACION_ARS': 'Servicio Rep. (Efectivo)',
                'INGRESO_COBRO_DEUDA_ARS': 'Cobro Deuda (Efectivo)',
                'PAGO_PROVEEDOR_ARS': 'Pago Proveedor (Efectivo)',
                'INGRESO_MANUAL_ARS': 'Ingreso Manual',
                'EGRESO_MANUAL_ARS': 'Egreso Manual'
            }
            if mov['tipo_transaccion'] in mapping:
                mov['tipo_transaccion'] = mapping[mov['tipo_transaccion']]

        elif mov['categoria'] == 'CUENTA_CORRIENTE':
            # Mostrar el método de pago en la descripción si no es obvio
            mov['tipo_transaccion'] += f" ({mov['metodo_pago_filtro']})"

        movimientos_diarios.append(mov)

    return render_template('reportes/libro_diario.html', 
                           movimientos=movimientos_diarios, 
                           start_date=start_date, 
                           end_date=end_date_display,
                           filtro_forma_pago=filtro_forma_pago,
                           filtro_origen=filtro_origen)
# ... (otras importaciones y código) ...

# ... (otras importaciones y código) ...

@app.route('/reportes/listado_diario')
@login_required
def listado_diario():
    selected_date_str = request.args.get('selected_date', datetime.now().strftime('%Y-%m-%d'))
    selected_date_start = selected_date_str + " 00:00:00"
    selected_date_end = selected_date_str + " 23:59:59"

    # Movimientos de caja (filtrados por el día seleccionado)
    # --- CAMBIO EN LA CONSULTA: Incluir sub_categoria ---
    caja_movs = db_query("""
        SELECT cm.*, u.username
        FROM caja_movimientos cm
        JOIN users u ON cm.user_id = u.id
        WHERE cm.fecha BETWEEN ? AND ?
        ORDER BY cm.fecha ASC
    """, (selected_date_start, selected_date_end))

    total_ingresos_caja_ars = sum(m['monto_ars'] for m in caja_movs if m['monto_ars'] is not None and m['monto_ars'] > 0 and ('INGRESO' in m['tipo'] and 'CIERRE' not in m['tipo'] and 'APERTURA' not in m['tipo'] and 'PAGO_PROVEEDOR' not in m['tipo']) )
    total_egresos_caja_ars = sum(m['monto_ars'] for m in caja_movs if m['monto_ars'] is not None and m['monto_ars'] > 0 and ('EGRESO' in m['tipo'] or 'PAGO_PROVEEDOR_ARS' == m['tipo']))

    total_ingresos_caja_usd = sum(m['monto_usd'] for m in caja_movs if m['monto_usd'] is not None and m['monto_usd'] > 0 and ('INGRESO' in m['tipo'] and 'CIERRE' not in m['tipo'] and 'APERTURA' not in m['tipo']))
    total_egresos_caja_usd = sum(m['monto_usd'] for m in caja_movs if m['monto_usd'] is not None and m['monto_usd'] > 0 and ('EGRESO' in m['tipo'] or 'PAGO_PROVEEDOR_USD' == m['tipo']))

    # Información de arqueo si hay uno para ese día y usuario
    raw_arqueo_info = db_query("""
        SELECT * FROM arqueo_caja 
        WHERE user_id = ? AND fecha_apertura LIKE ?
    """, (current_user.id, selected_date_str + '%'))

    # ## MODIFICACIÓN CLAVE ##: Convertir sqlite3.Row a un diccionario y asegurar valores por defecto.
    # Usamos una función auxiliar para obtener un valor numérico seguro.
    def get_numeric_value(data_dict, key, default_value=0.0):
        value = data_dict.get(key)
        return float(value) if value is not None else default_value

    if raw_arqueo_info:
        arqueo_info = dict(raw_arqueo_info[0])
    else:
        # Proporcionar valores por defecto para todas las claves que la plantilla pueda necesitar
        arqueo_info = {
            'monto_inicial_ars': 0.0,
            'monto_sistema_calculado_ars': 0.0,
            'monto_contado_fisico_ars': 0.0,
            'diferencia_ars': 0.0,
            'monto_inicial_usd': 0.0,
            'monto_sistema_calculado_usd': 0.0,
            'monto_contado_fisico_usd': 0.0,
            'diferencia_usd': 0.0,
            'estado': 'N/A',
            'fecha_apertura': 'N/A',
            'fecha_cierre': 'N/A',
            'observaciones': 'No se encontró arqueo de caja para la fecha seleccionada.'
        }
    
    # ## MODIFICACIÓN CLAVE ##: Usar la función auxiliar para obtener valores numéricos seguros
    arqueo_info['monto_inicial_ars'] = get_numeric_value(arqueo_info, 'monto_inicial_ars')
    arqueo_info['monto_sistema_calculado_ars'] = get_numeric_value(arqueo_info, 'monto_sistema_calculado_ars')
    arqueo_info['monto_contado_fisico_ars'] = get_numeric_value(arqueo_info, 'monto_contado_fisico_ars')
    arqueo_info['diferencia_ars'] = get_numeric_value(arqueo_info, 'diferencia_ars')
    arqueo_info['monto_inicial_usd'] = get_numeric_value(arqueo_info, 'monto_inicial_usd')
    arqueo_info['monto_sistema_calculado_usd'] = get_numeric_value(arqueo_info, 'monto_sistema_calculado_usd')
    arqueo_info['monto_contado_fisico_usd'] = get_numeric_value(arqueo_info, 'monto_contado_fisico_usd')
    arqueo_info['diferencia_usd'] = get_numeric_value(arqueo_info, 'diferencia_usd')


    # Movimientos generales del sistema (auditoría)
    system_movs_query = """
        SELECT m.fecha, u.username, m.tipo_movimiento, m.tipo_item, m.detalles
        FROM movimientos m
        JOIN users u ON m.user_id = u.id
        WHERE m.fecha BETWEEN ? AND ?
        ORDER BY m.fecha ASC
    """
    raw_system_movs = db_query(system_movs_query, (selected_date_start, selected_date_end))

    system_movs = []
    for mov_row in raw_system_movs:
        mov = dict(mov_row)
        if mov['detalles']:
            try:
                mov['detalles_parsed'] = json.loads(mov['detalles'])
            except json.JSONDecodeError:
                mov['detalles_parsed'] = {'Error': 'No se pudo leer el detalle.'}
        else:
            mov['detalles_parsed'] = {}
        system_movs.append(mov)
            
    return render_template('reportes/listado_diario.html', 
                           selected_date=selected_date_str,
                           system_movs=system_movs,
                           caja_movs=caja_movs,
                           total_ingresos_caja_ars=total_ingresos_caja_ars,
                           total_egresos_caja_ars=total_egresos_caja_ars,
                           total_ingresos_caja_usd=total_ingresos_caja_usd,
                           total_egresos_caja_usd=total_egresos_caja_usd,
                           arqueo_info=arqueo_info)

# ... (resto del código de app.py) ...
# ... (resto del código de app.py) ...
@app.route('/backup/database')
@login_required
@admin_required
def backup_database():
    try:
        app_root = app.root_path
        ruta_db = os.path.join(app_root, DB_NAME)
        fecha_actual = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        nombre_backup = f"backup_negocio_erp_{fecha_actual}.db"
        
        return send_file(
            ruta_db,
            as_attachment=True,
            download_name=nombre_backup,
            mimetype='application/x-sqlite3'
        )
    except FileNotFoundError:
        flash("Error: No se encontró el archivo de la base de datos para realizar el backup.", "danger")
        return redirect(url_for('reportes_menu'))
    except Exception as e:
        app.logger.error(f"Ocurrió un error inesperado al generar el backup: {e}", exc_info=True)
        flash(f"Ocurrió un error inesperado al generar el backup: {e}", "danger")
        return redirect(url_for('reportes_menu'))

@app.route('/exportar/ventas')
@login_required
def exportar_ventas():
    start_date, _, end_date_query = get_date_filters()
    query = "SELECT v.id, v.fecha_venta, c.marca, c.modelo, c.imei, v.precio_final_ars, v.precio_final_usd, v.valor_dolar_momento, v.impuestos_pct, v.ganancia_pct, p.nombre, p.apellido, p.razon_social, p.cuit_cuil FROM ventas v JOIN celulares c ON v.celular_id = c.id JOIN personas p ON v.cliente_id = p.id WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ? ORDER BY v.fecha_venta DESC"
    ventas = db_query(query, (start_date, end_date_query))
    
    output = io.StringIO()
    output.write('\ufeff') # SOLUCIÓN 1: Agregar BOM para Excel
    writer = csv.writer(output, delimiter=';')
    
    # SOLUCIÓN 2: Cambiamos 'ID Venta' por 'Nro_Venta' (Evita el error SYLK)
    writer.writerow(['Nro_Venta', 'Fecha', 'Marca', 'Modelo', 'IMEI', 'Cliente', 'CUIT/CUIL Cliente', 'Precio Final ARS', 'Precio Final USD', 'Impuestos %', 'Ganancia %', 'Dolar Momento'])
    
    for venta in ventas:
        cliente_nombre = venta['razon_social'] or f"{venta['nombre']} {venta['apellido']}"
        writer.writerow([venta['id'], venta['fecha_venta'], venta['marca'], venta['modelo'], venta['imei'], cliente_nombre, venta['cuit_cuil'], venta['precio_final_ars'], venta['precio_final_usd'], venta['impuestos_pct'], venta['ganancia_pct'], venta['valor_dolar_momento']])
    
    output.seek(0)
    return Response(output, mimetype="text/csv", headers={"Content-Disposition":f"attachment;filename=reporte_ventas_{start_date}.csv"})





@app.route('/exportar/rentabilidad')
@login_required
@admin_required
def exportar_rentabilidad():
    start_date, _, end_date_query = get_date_filters()
    
    dolar_info_from_context = inject_dolar_values()
    valor_dolar_compra_local = dolar_info_from_context['valor_dolar_compra'] 
    if valor_dolar_compra_local is None or valor_dolar_compra_local == 0:
        valor_dolar_compra_local = 1.0 # Fallback
    
    # Ventas de celulares
    ventas_data = db_query("SELECT c.marca, c.modelo, c.imei, v.precio_final_ars, (c.costo_usd * v.valor_dolar_momento) as costo_ars FROM ventas v JOIN celulares c ON v.celular_id = c.id WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?", (start_date, end_date_query))
    
    # Reparaciones
    # MODIFICACIÓN: Para exportar rentabilidad, asegurarse de usar el costo real (r.costo_usd)
    reparaciones_data = db_query("""
        SELECT
            sr.id, sr.fecha_servicio, sr.imei_equipo, sr.precio_final_ars, sr.precio_mano_obra_ars,
            COALESCE(SUM(ru.cantidad * r.costo_usd), 0) AS costo_repuestos_reales_usd_sum
        FROM servicios_reparacion sr
        LEFT JOIN repuestos_usados ru ON sr.id = ru.servicio_id
        LEFT JOIN repuestos r ON ru.repuesto_id = r.id AND ru.manual_item_nombre IS NULL
        WHERE sr.status = 'COMPLETADO' AND sr.fecha_servicio BETWEEN ? AND ?
        GROUP BY sr.id
    """, (start_date, end_date_query))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Tipo', 'ID/IMEI', 'Fecha', 'Descripción', 'Ingreso ARS', 'Costo ARS', 'Ganancia Bruta ARS'])
    
    for v in ventas_data:
        ganancia = (v['precio_final_ars'] or 0) - (v['costo_ars'] or 0)
        writer.writerow(['Venta', v['imei'], v['fecha_venta'], f"{v['marca']} {v['modelo']}", f"{v['precio_final_ars']:.2f}", f"{v['costo_ars']:.2f}", f"{ganancia:.2f}"])
    
    for r in reparaciones_data:
        costo_repuestos_ars = (r['costo_repuestos_reales_usd_sum'] or 0) * valor_dolar_compra_local
        ganancia = (r['precio_final_ars'] or 0) - costo_repuestos_ars
        writer.writerow(['Servicio', r['imei_equipo'], r['fecha_servicio'], f"Reparación (ID {r['id']})", f"{r['precio_final_ars']:.2f}", f"{costo_repuestos_ars:.2f}", f"{ganancia:.2f}"])
    
    output.seek(0)
    return Response(output, mimetype="text/csv", headers={"Content-Disposition":f"attachment;filename=reporte_rentabilidad_{start_date}.csv"})

# --- API interna ---
@app.route('/api/dolar')
@login_required
def api_get_dolar():
    dolar_info = obtener_cotizacion_dolar()
    
    # Verificamos que los datos existan (manteniendo tu validación original de no ser None)
    if dolar_info['compra'] is not None and dolar_info['venta'] is not None:
        return jsonify({
            'compra': dolar_info['compra'],          # BCRA Compra
            'venta': dolar_info['venta'],            # BCRA Venta
            'compra_blue': dolar_info['compra_blue'], # Dólar Hoy Compra
            'venta_blue': dolar_info['venta_blue']    # Dólar Hoy Venta
        })
        
    return jsonify({'error': 'Cotización no disponible'}), 503
@app.route('/api/repuesto/<int:repuesto_id>')
@login_required
def api_get_repuesto(repuesto_id):
    # MODIFICACIÓN: Esta API ahora no se usa para obtener UN repuesto.
    # La API para Select2 es `api_buscar_repuestos`.
    # Si esta ruta se usara, debería devolver los precios de venta también.
    # Por ahora, se mantiene pero se desaconseja su uso para presupuestos.
    repuesto = db_query("SELECT id, nombre_parte, modelo_compatible, costo_usd, stock, precio_venta_ars, precio_venta_usd FROM repuestos WHERE id = ?", (repuesto_id,))
    if repuesto:
        return jsonify(dict(repuesto[0])) # Ensure it's a dict
    return jsonify({'error': 'Repuesto no encontrado'}), 404

@app.route('/api/personas/buscar')
@login_required
def api_buscar_personas():
    query_str = request.args.get('q', '').strip()
    rol = request.args.get('rol', '').strip() # 'cliente' o 'proveedor'
    
    if not query_str:
        return jsonify([])

    sql_query = "SELECT id, nombre, apellido, razon_social, cuit_cuil FROM personas WHERE (nombre LIKE ? OR apellido LIKE ? OR razon_social LIKE ? OR cuit_cuil LIKE ?)"
    params = [f"%{query_str}%", f"%{query_str}%", f"%{query_str}%", f"%{query_str}%"]

    if rol == 'cliente':
        sql_query += " AND es_cliente = 1"
    elif rol == 'proveedor':
        sql_query += " AND es_proveedor = 1"
    
    sql_query += " LIMIT 10" # Limitar resultados para rendimiento

    resultados = db_query(sql_query, tuple(params))
    
    # Formatear resultados para el autocompletado
    formatted_results = []
    for r in resultados:
        display_name = r['razon_social'] or f"{r['nombre']} {r['apellido']}"
        formatted_results.append({
            'id': r['id'],
            'text': f"{display_name} (CUIT/CUIL: {r['cuit_cuil']})"
        })
    return jsonify(formatted_results)

@app.route('/api/buscar_celulares_disponibles')
@login_required
def api_buscar_celulares_disponibles():
    """
    API para buscar celulares disponibles (stock > 0) para el select2.
    Permite buscar por 'q' (query general) o por 'id' (para precargar).
    """
    q = request.args.get('q', '').strip().lower()
    cel_id = request.args.get('id', type=int)

    query_params = []
    sql_query = "SELECT id, marca, modelo, imei, condicion, costo_usd FROM celulares WHERE stock = 1" # Solo los disponibles en stock para venta

    if cel_id:
        sql_query += " AND id = ?"
        query_params.append(cel_id)
    elif q and (len(q) >= 1 or q == 'all_cells_trigger'):
        if q == 'all_cells_trigger':
            pass 
        else:
            sql_query += " AND (LOWER(marca) LIKE ? OR LOWER(modelo) LIKE ? OR imei LIKE ?)"
            query_params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    else: 
        return jsonify(results=[])
        
    sql_query += " ORDER BY marca, modelo LIMIT 50"

    celulares = db_query(sql_query, tuple(query_params))

    formatted_results = []
    for cel in celulares:
        formatted_results.append({
            'id': cel['id'],
            'text': f"{cel['marca']} {cel['modelo']} ({cel['condicion']}, IMEI: {cel['imei']}, Costo: ${cel['costo_usd']:.2f})",
            'costo_usd': cel['costo_usd'] 
        })
    
    return jsonify(results=formatted_results)

## NUEVAS MODIFICACIONES ##
@app.route('/api/buscar_celulares_parte_pago')
@login_required
def api_buscar_celulares_parte_pago():
    """
    API para buscar celulares que han sido entregados como parte de pago y están PENDIENTES
    de ser asociados a una venta confirmada (es decir, tienen es_parte_pago = 1 y stock = 0).
    """
    q = request.args.get('q', '').strip().lower()
    cel_id = request.args.get('id', type=int) 

    query_params = []
    sql_query = """
        SELECT id, marca, modelo, imei, condicion, costo_usd, es_parte_pago, stock
        FROM celulares
        WHERE es_parte_pago = 1 AND stock = 0 -- Celulares entregados como parte de pago y pendientes de confirmación/reingreso
    """

    if cel_id:
        sql_query += " AND id = ?"
        query_params.append(cel_id)
    elif q and (len(q) >= 1 or q == 'all_cells_trigger'):
        if q == 'all_cells_trigger':
            pass 
        else:
            sql_query += " AND (LOWER(marca) LIKE ? OR LOWER(modelo) LIKE ? OR imei LIKE ?)"
            query_params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    else:
        return jsonify(results=[])
        
    sql_query += " ORDER BY marca, modelo LIMIT 50"

    celulares = db_query(sql_query, tuple(query_params))

    # Obtener el valor del dólar de compra para mostrar el "Costo Sugerido" en ARS
    dolar_info_from_context = inject_dolar_values()
    valor_dolar_compra_local = dolar_info_from_context['valor_dolar_compra'] 
    if valor_dolar_compra_local is None or valor_dolar_compra_local == 0:
        valor_dolar_compra_local = 1.0 # Fallback para cálculos

    formatted_results = []
    for cel in celulares:
        formatted_results.append({
            'id': cel['id'],
            'text': f"{cel['marca']} {cel['modelo']} ({cel['condicion']}, IMEI: {cel['imei']}) [VALOR INICIAL: ARS {(cel['costo_usd'] * valor_dolar_compra_local):.2f}]", # Mostrar el costo inicial en ARS
            'imei': cel['imei'],
            'costo_usd': cel['costo_usd'] # Para facilitar el cálculo del valor inicial en el frontend
        })
    
    return jsonify(results=formatted_results)

## NUEVAS MODIFICACIONES SERVICIO TÉCNICO: API para buscar repuestos (para Select2) ##
@app.route('/api/buscar_repuestos')
@login_required
def api_buscar_repuestos():
    q = request.args.get('q', '').strip().lower()
    rep_id = request.args.get('id', type=int)

    query_params = []
    # MODIFICACIÓN CLAVE: Seleccionar precio_venta_ars y precio_venta_usd
    sql_query = "SELECT id, nombre_parte, modelo_compatible, stock, costo_usd, precio_venta_ars, precio_venta_usd FROM repuestos"

    if rep_id:
        sql_query += " WHERE id = ?"
        query_params.append(rep_id)
    elif q: 
        sql_query += " WHERE (LOWER(nombre_parte) LIKE ? OR LOWER(modelo_compatible) LIKE ?)"
        query_params.extend([f"%{q}%", f"%{q}%"])

    sql_query += " ORDER BY nombre_parte LIMIT 50" 

    repuestos = db_query(sql_query, tuple(query_params))

    formatted_results = []
    for rep in repuestos:
        formatted_results.append({
            'id': rep['id'],
            # MODIFICACIÓN: Mostrar el precio de venta en USD en el texto del select2
            'text': f"{rep['nombre_parte']} ({rep['modelo_compatible'] or 'Genérico'}) - Stock: {rep['stock']}, PV: USD {rep['precio_venta_usd']:.2f}",
            'costo_usd': rep['costo_usd'], # Se mantiene por si es necesario para otros reportes/log
            'stock': rep['stock'],
            'precio_venta_ars': rep['precio_venta_ars'],
            'precio_venta_usd': rep['precio_venta_usd'] # <-- MODIFICACIÓN CLAVE: Asegurar que se envíe este valor
        })
    
    return jsonify(results=formatted_results)


@app.route('/exportar/libro_diario')
@login_required
def exportar_libro_diario():
    # 1. Obtener los mismos filtros que usa la vista
    start_date, _, end_date_query = get_date_filters()
    filtro_forma_pago = request.args.get('forma_pago', '')
    filtro_origen = request.args.get('origen', '')

    params = []

    # 2. Reutilizar la consulta SQL del Libro Diario (UNION ALL de 4 partes)
    query = """
    SELECT * FROM (
        -- 1. Movimientos generales del sistema
        SELECT 
            m.fecha, 
            u.username, 
            m.tipo_movimiento AS tipo_transaccion, 
            m.tipo_item AS categoria, 
            m.detalles,
            NULL AS monto_ingreso_ars,
            NULL AS monto_egreso_ars,
            NULL AS monto_ingreso_usd,
            NULL AS monto_egreso_usd,
            NULL AS sub_categoria,
            'OTRO' AS metodo_pago_filtro,
            'SISTEMA' AS origen_filtro
        FROM movimientos m
        JOIN users u ON m.user_id = u.id
        WHERE m.fecha BETWEEN ? AND ?

        UNION ALL

        -- 2. Movimientos de caja
        SELECT 
            cm.fecha, 
            u.username, 
            cm.tipo AS tipo_transaccion, 
            'CAJA' AS categoria, 
            cm.descripcion AS detalles,
            CASE WHEN cm.monto_ars > 0 AND (cm.tipo LIKE 'INGRESO%' OR cm.tipo = 'APERTURA_CAJA_ARS') THEN cm.monto_ars ELSE NULL END,
            CASE WHEN cm.monto_ars > 0 AND (cm.tipo LIKE 'EGRESO%' OR cm.tipo = 'PAGO_PROVEEDOR_ARS' OR cm.tipo = 'CIERRE_CAJA_ARS') THEN cm.monto_ars ELSE NULL END,
            CASE WHEN cm.monto_usd > 0 AND (cm.tipo LIKE 'INGRESO%' OR cm.tipo = 'APERTURA_CAJA_USD') THEN cm.monto_usd ELSE NULL END,
            CASE WHEN cm.monto_usd > 0 AND (cm.tipo LIKE 'EGRESO%' OR cm.tipo = 'PAGO_PROVEEDOR_USD' OR cm.tipo = 'CIERRE_CAJA_USD') THEN cm.monto_usd ELSE NULL END,
            cm.sub_categoria,
            COALESCE(cm.metodo_pago, 'EFECTIVO') AS metodo_pago_filtro,
            CASE 
                WHEN cm.tipo LIKE '%PROVEEDOR%' THEN 'PROVEEDOR'
                WHEN cm.tipo LIKE '%VENTA%' OR cm.tipo LIKE '%COBRO%' OR cm.tipo LIKE '%SERVICIO%' THEN 'CLIENTE'
                ELSE 'CAJA_INTERNA' 
            END AS origen_filtro
        FROM caja_movimientos cm
        JOIN users u ON cm.user_id = u.id
        WHERE cm.fecha BETWEEN ? AND ?

        UNION ALL
        
        -- 3. Pagos a Proveedores (No Efectivo)
        SELECT
            pp.fecha_pago AS fecha,
            u.username,
            'PAGO_PROVEEDOR' AS tipo_transaccion,
            'CUENTA_CORRIENTE' AS categoria,
            'Pago a Prov. ' || COALESCE(p.razon_social, p.nombre || ' ' || p.apellido) || ' (' || pp.observaciones || ')' AS detalles,
            NULL AS monto_ingreso_ars,
            pp.monto_ars AS monto_egreso_ars,
            NULL AS monto_ingreso_usd,
            pp.monto_usd AS monto_egreso_usd,
            'Pago Proveedores' AS sub_categoria,
            pp.tipo_pago AS metodo_pago_filtro,
            'PROVEEDOR' AS origen_filtro
        FROM pagos_proveedores pp
        JOIN users u ON pp.user_id = u.id
        JOIN personas p ON pp.proveedor_id = p.id
        WHERE pp.fecha_pago BETWEEN ? AND ? AND pp.tipo_pago NOT IN ('EFECTIVO_ARS', 'EFECTIVO_USD', 'EFECTIVO_ARS_USD_COMBINADO')

        UNION ALL

        -- 4. Cobros a Clientes (No Efectivo)
        SELECT
            cc.fecha_cobro AS fecha,
            u.username,
            'COBRO_CLIENTE' AS tipo_transaccion,
            'CUENTA_CORRIENTE' AS categoria,
            'Cobro a Cliente ' || COALESCE(p.razon_social, p.nombre || ' ' || p.apellido) || ' (' || cc.observaciones || ')' AS detalles,
            cc.monto_ars AS monto_ingreso_ars,
            NULL AS monto_egreso_ars,
            cc.monto_usd AS monto_ingreso_usd,
            NULL AS monto_egreso_usd,
            'Cobro Clientes' AS sub_categoria,
            cc.metodo_pago AS metodo_pago_filtro,
            'CLIENTE' AS origen_filtro
        FROM cobros_clientes cc
        JOIN users u ON cc.user_id = u.id
        JOIN personas p ON cc.cliente_id = p.id
        WHERE cc.fecha_cobro BETWEEN ? AND ? AND cc.metodo_pago NOT IN ('EFECTIVO', 'EFECTIVO_ARS', 'EFECTIVO_USD')

    ) AS unificado
    WHERE 1=1
    """
    
    # Añadir parámetros de fecha para los 4 SELECTs
    params.extend([start_date, end_date_query] * 4)

    # Aplicar filtros dinámicos (igual que en la vista)
    if filtro_forma_pago:
        query += " AND unificado.metodo_pago_filtro LIKE ?"
        params.append(f"%{filtro_forma_pago}%")
    
    if filtro_origen:
        if filtro_origen == 'PROVEEDOR':
            query += " AND unificado.origen_filtro = 'PROVEEDOR'"
        elif filtro_origen == 'CLIENTE':
            query += " AND unificado.origen_filtro = 'CLIENTE'"

    query += " ORDER BY fecha DESC"
    
    movimientos = db_query(query, tuple(params))

    # 3. Generar CSV
    output = io.StringIO()
    # Agregar BOM para que Excel reconozca tildes y caracteres especiales correctamente
    output.write('\ufeff') 
    
    writer = csv.writer(output, delimiter=';') # Usamos punto y coma que es más común en Excel español
    
    # Encabezados
    writer.writerow(['Fecha', 'Usuario', 'Transacción', 'Categoría', 'Sub-Categoría', 'Método Pago', 'Detalles', 'Ingreso ARS', 'Egreso ARS', 'Ingreso USD', 'Egreso USD'])

    for mov in movimientos:
        # Limpieza de detalles (si es JSON)
        detalles_texto = str(mov['detalles'])
        if '{' in detalles_texto and '}' in detalles_texto:
            try:
                # Intentar hacerlo más legible si es JSON
                import json
                json_data = json.loads(detalles_texto)
                # Convertir dict a string plano clave:valor
                detalles_texto = ", ".join([f"{k}: {v}" for k, v in json_data.items()])
            except:
                pass # Si falla, dejar el texto original

        writer.writerow([
            mov['fecha'],
            mov['username'],
            mov['tipo_transaccion'],
            mov['categoria'],
            mov['sub_categoria'] or '',
            mov['metodo_pago_filtro'],
            detalles_texto,
            f"{mov['monto_ingreso_ars']:.2f}" if mov['monto_ingreso_ars'] else '',
            f"{mov['monto_egreso_ars']:.2f}" if mov['monto_egreso_ars'] else '',
            f"{mov['monto_ingreso_usd']:.2f}" if mov['monto_ingreso_usd'] else '',
            f"{mov['monto_egreso_usd']:.2f}" if mov['monto_egreso_usd'] else ''
        ])

    output.seek(0)
    filename = f"Libro_Diario_{start_date}_al_{end_date_query.split()[0]}.csv"
    
    return Response(
        output, 
        mimetype="text/csv", 
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


@app.route('/exportar/auditoria')
@login_required
@admin_required
def exportar_auditoria():
    # 1. Obtener filtros
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_usuario = request.args.get('usuario', '')
    filtro_movimiento = request.args.get('movimiento', '').strip()
    filtro_item_tipo = request.args.get('item_tipo', '').strip()
    filtro_detalles = request.args.get('detalles', '').strip()

    # 2. Construcción de la consulta (Idéntica a reporte_auditoria)
    query_parts = []
    date_params_for_union = [start_date, end_date_query]

    # Parte A: Tabla movimientos
    query_parts.append("""
        SELECT
            m.fecha,
            u.username,
            m.tipo_movimiento,
            m.tipo_item,
            m.detalles,
            NULL AS sub_categoria,
            'GENERAL' AS source_table
        FROM movimientos m
        JOIN users u ON m.user_id = u.id
        WHERE m.fecha BETWEEN ? AND ?
    """)

    # Parte B: Tabla caja_movimientos
    query_parts.append("""
        SELECT
            cm.fecha,
            u.username,
            cm.tipo AS tipo_movimiento,
            'CAJA' AS tipo_item,
            CASE
                WHEN cm.sub_categoria IS NOT NULL AND cm.sub_categoria != '' THEN JSON_OBJECT('Descripción', cm.descripcion, 'Sub-Rubro', cm.sub_categoria)
                ELSE JSON_OBJECT('Descripción', cm.descripcion)
            END AS detalles,
            cm.sub_categoria,
            'CAJA' AS source_table
        FROM caja_movimientos cm
        JOIN users u ON cm.user_id = u.id
        WHERE cm.fecha BETWEEN ? AND ?
    """)

    full_query_base = " UNION ALL ".join(query_parts)
    final_params = date_params_for_union * len(query_parts)

    # 3. Aplicación de filtros dinámicos
    where_clauses_dynamic = []
    filter_params_dynamic = []

    if filtro_usuario:
        where_clauses_dynamic.append("user_id = ?")
        filter_params_dynamic.append(filtro_usuario)
    
    if filtro_movimiento:
        where_clauses_dynamic.append("tipo_movimiento LIKE ?")
        filter_params_dynamic.append(f"%{filtro_movimiento}%")

    if filtro_item_tipo:
        where_clauses_dynamic.append("tipo_item = ?")
        filter_params_dynamic.append(filtro_item_tipo)

    if filtro_detalles:
        where_clauses_dynamic.append("detalles LIKE ?")
        filter_params_dynamic.append(f"%{filtro_detalles}%")

    if where_clauses_dynamic:
        final_query = f"SELECT fecha, username, tipo_movimiento, tipo_item, detalles, sub_categoria, source_table FROM ({full_query_base}) AS combined_data WHERE {' AND '.join(where_clauses_dynamic)}"
        final_params.extend(filter_params_dynamic)
    else:
        final_query = f"SELECT fecha, username, tipo_movimiento, tipo_item, detalles, sub_categoria, source_table FROM ({full_query_base}) AS combined_data"

    final_query += " ORDER BY fecha DESC"
    
    movimientos = db_query(final_query, tuple(final_params))

    # 4. Generar CSV
    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel
    writer = csv.writer(output, delimiter=';')
    
    # Encabezados
    writer.writerow(['Fecha', 'Usuario', 'Tipo Movimiento', 'Tipo Item', 'Sub-Categoría', 'Origen', 'Detalles'])

    for mov in movimientos:
        # Formatear detalles (JSON a String legible)
        detalles_texto = str(mov['detalles'])
        if '{' in detalles_texto:
            try:
                import json
                json_data = json.loads(detalles_texto)
                detalles_texto = " | ".join([f"{k}: {v}" for k, v in json_data.items()])
            except:
                pass

        writer.writerow([
            mov['fecha'],
            mov['username'],
            mov['tipo_movimiento'],
            mov['tipo_item'] or '-',
            mov['sub_categoria'] or '-',
            mov['source_table'],
            detalles_texto
        ])

    output.seek(0)
    filename = f"Auditoria_{start_date}_al_{end_date_display}.csv"
    
    return Response(
        output, 
        mimetype="text/csv", 
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


@app.route('/exportar/inventario/celulares')
@login_required
def exportar_inventario_celulares():
    # Obtenemos solo los celulares que están en stock (stock = 1)
    query = "SELECT marca, modelo, imei, condicion, almacenamiento_gb, ram_gb, color, bateria_salud, costo_usd, observaciones FROM celulares WHERE stock = 1 ORDER BY marca, modelo ASC"
    celulares = db_query(query)
    
    output = io.StringIO()
    output.write('\ufeff') # BOM para que Excel reconozca tildes
    writer = csv.writer(output, delimiter=';')
    
    # Encabezados
    writer.writerow(['Marca', 'Modelo', 'IMEI', 'Condición', 'Alm. (GB)', 'RAM (GB)', 'Color', 'Batería %', 'Costo USD', 'Observaciones'])
    
    for c in celulares:
        writer.writerow([
            c['marca'],
            c['modelo'],
            c['imei'],
            c['condicion'],
            c['almacenamiento_gb'],
            c['ram_gb'],
            c['color'],
            c['bateria_salud'] if c['bateria_salud'] else 'N/A',
            f"{c['costo_usd']:.2f}",
            c['observaciones']
        ])
    
    output.seek(0)
    fecha = datetime.now().strftime('%Y-%m-%d')
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=stock_celulares_{fecha}.csv"}
    )

@app.route('/exportar/inventario/repuestos')
@login_required
def exportar_inventario_repuestos():
    # Obtenemos productos, repuestos y accesorios con stock > 0
    query = "SELECT categoria, nombre_parte, modelo_compatible, stock, costo_usd, precio_venta_ars, precio_venta_usd FROM repuestos WHERE stock > 0 ORDER BY categoria, nombre_parte ASC"
    items = db_query(query)
    
    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel
    writer = csv.writer(output, delimiter=';')
    
    # Encabezados
    writer.writerow(['Categoría', 'Producto/Repuesto', 'Compatibilidad', 'Stock', 'Costo Unit USD', 'Precio Venta ARS', 'Precio Venta USD'])
    
    for i in items:
        writer.writerow([
            i['categoria'],
            i['nombre_parte'],
            i['modelo_compatible'],
            i['stock'],
            f"{i['costo_usd']:.2f}",
            f"{i['precio_venta_ars']:.2f}",
            f"{i['precio_venta_usd']:.2f}"
        ])
    
    output.seek(0)
    fecha = datetime.now().strftime('%Y-%m-%d')
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=stock_productos_{fecha}.csv"}
    )



@app.route('/exportar/compras')
@login_required
@admin_required
def exportar_compras():
    # Obtener los mismos filtros de la vista
    start_date, _, end_date_query = get_date_filters()
    filtro_proveedor = request.args.get('proveedor', '')
    filtro_tipo_item = request.args.get('tipo_item', '')
    filtro_estado_pago = request.args.get('estado_pago', '')

    query = """
        SELECT co.*, p.razon_social, p.nombre, p.apellido, u.username,
               CASE
                   WHEN co.tipo_item = 'CELULAR' THEN c.marca || ' ' || c.modelo || ' (IMEI: ' || COALESCE(c.imei, 'N/A') || ')'
                   WHEN co.tipo_item = 'REPUESTO' THEN r.nombre_parte || ' (' || COALESCE(r.modelo_compatible, 'Genérico') || ')'
                   ELSE 'Desconocido'
               END AS item_descripcion
        FROM compras co
        JOIN personas p ON co.proveedor_id = p.id
        JOIN users u ON co.user_id = u.id
        LEFT JOIN celulares c ON co.item_id = c.id AND co.tipo_item = 'CELULAR'
        LEFT JOIN repuestos r ON co.item_id = r.id AND co.tipo_item = 'REPUESTO'
        WHERE co.fecha_compra BETWEEN ? AND ?
    """
    params = [start_date, end_date_query]

    if filtro_proveedor:
        query += " AND p.id = ?"
        params.append(filtro_proveedor)
    if filtro_tipo_item:
        query += " AND co.tipo_item = ?"
        params.append(filtro_tipo_item)
    if filtro_estado_pago:
        query += " AND co.estado_pago = ?"
        params.append(filtro_estado_pago)

    query += " ORDER BY co.fecha_compra DESC"
    compras = db_query(query, tuple(params))

    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel
    writer = csv.writer(output, delimiter=';')
    
    # Encabezados
    writer.writerow([
        'ID Compra', 'Fecha', 'Proveedor', 'Usuario', 'Tipo', 
        'Descripción Ítem', 'Cantidad', 'Costo Unit USD', 
        'Costo Total USD', 'Dólar Momento', 'Costo Total ARS', 'Estado Pago'
    ])

    for co in compras:
        proveedor_nombre = co['razon_social'] or f"{co['nombre']} {co['apellido']}"
        writer.writerow([
            co['id'],
            co['fecha_compra'],
            proveedor_nombre,
            co['username'],
            co['tipo_item'],
            co['item_descripcion'],
            co['cantidad'],
            f"{co['costo_unitario_usd']:.2f}",
            f"{co['costo_total_usd']:.2f}",
            f"{co['valor_dolar_momento']:.2f}",
            f"{co['costo_total_ars']:.2f}",
            co['estado_pago']
        ])

    output.seek(0)
    filename = f"compras_{start_date}_al_{datetime.now().strftime('%Y-%m-%d')}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )




@app.route('/ventas/historial')
@login_required
def historial_ventas():
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_cliente = request.args.get('cliente', '')
    filtro_producto = request.args.get('producto', '').strip()

    query = """
        SELECT v.*, c.marca, c.modelo, c.imei, p.nombre, p.apellido, p.razon_social 
        FROM ventas v 
        JOIN celulares c ON v.celular_id = c.id 
        JOIN personas p ON v.cliente_id = p.id 
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
    """
    params = [start_date, end_date_query]

    if filtro_cliente:
        query += " AND p.id = ?"
        params.append(filtro_cliente)
    if filtro_producto:
        query += " AND (c.marca LIKE ? OR c.modelo LIKE ? OR c.imei LIKE ?)"
        params.extend([f"%{filtro_producto}%", f"%{filtro_producto}%", f"%{filtro_producto}%"])

    query += " ORDER BY v.fecha_venta DESC"
    ventas = db_query(query, tuple(params))
    
    clientes_disponibles = db_query("SELECT id, nombre, apellido, razon_social FROM personas WHERE es_cliente = 1 ORDER BY razon_social, apellido")

    return render_template('ventas/historial_ventas.html', 
                           ventas=ventas,
                           start_date=start_date, 
                           end_date=end_date_display,
                           filtros_activos={'cliente': filtro_cliente, 'producto': filtro_producto},
                           clientes_disponibles=clientes_disponibles)

# NUEVA RUTA: Exportar historial con filtros aplicados
@app.route('/exportar/ventas/historial')
@login_required
def exportar_ventas_historial():
    start_date, _, end_date_query = get_date_filters()
    filtro_cliente = request.args.get('cliente', '')
    filtro_producto = request.args.get('producto', '').strip()

    query = """
        SELECT v.id, v.fecha_venta, c.marca, c.modelo, c.imei, 
               v.precio_final_ars, v.precio_final_usd, v.valor_dolar_momento, 
               p.nombre, p.apellido, p.razon_social, v.saldo_pendiente
        FROM ventas v 
        JOIN celulares c ON v.celular_id = c.id 
        JOIN personas p ON v.cliente_id = p.id 
        WHERE v.status = 'COMPLETADA' AND v.fecha_venta BETWEEN ? AND ?
    """
    params = [start_date, end_date_query]

    if filtro_cliente:
        query += " AND p.id = ?"
        params.append(filtro_cliente)
    if filtro_producto:
        query += " AND (c.marca LIKE ? OR c.modelo LIKE ? OR c.imei LIKE ?)"
        params.extend([f"%{filtro_producto}%", f"%{filtro_producto}%", f"%{filtro_producto}%"])

    query += " ORDER BY v.fecha_venta DESC"
    ventas = db_query(query, tuple(params))

    output = io.StringIO()
    output.write('\ufeff') # BOM para que Excel detecte UTF-8 y tildes
    writer = csv.writer(output, delimiter=';') # Punto y coma para Excel en español
    
    # Encabezado (Evitamos "ID" por error SYLK)
    writer.writerow(['Venta_ID', 'Fecha', 'Cliente', 'Equipo', 'IMEI', 'Precio Final ARS', 'Precio Final USD', 'Cotiz Dolar', 'Saldo Pendiente'])

    for v in ventas:
        cliente = v['razon_social'] or f"{v['nombre']} {v['apellido']}"
        
        # --- CORRECCIÓN ESPECÍFICA PARA PRECIO FINAL USD ---
        # 1. Obtenemos el valor (si es None usamos 0)
        val_usd = v['precio_final_usd'] if v['precio_final_usd'] is not None else 0.0
        # 2. Formateamos a 2 decimales y cambiamos punto por coma para Excel
        precio_usd_export = f"{val_usd:.2f}".replace('.', ',')
        
        # Hacemos lo mismo para los otros valores numéricos para que todo el reporte sea funcional
        precio_ars_export = f"{(v['precio_final_ars'] or 0.0):.2f}".replace('.', ',')
        dolar_export = f"{(v['valor_dolar_momento'] or 0.0):.2f}".replace('.', ',')
        saldo_export = f"{(v['saldo_pendiente'] or 0.0):.2f}".replace('.', ',')

        writer.writerow([
            v['id'], 
            v['fecha_venta'], 
            cliente, 
            f"{v['marca']} {v['modelo']}", 
            v['imei'],
            precio_ars_export, 
            precio_usd_export, # <--- Valor corregido
            dolar_export, 
            saldo_export
        ])

    output.seek(0)
    return Response(output, mimetype="text/csv", 
                    headers={"Content-Disposition": f"attachment;filename=historial_ventas_{start_date}.csv"})
    
    
    
@app.route('/exportar/reparaciones')
@login_required
def exportar_reparaciones():
    start_date, _, end_date_query = get_date_filters()
    query = """
        SELECT s.id, s.fecha_servicio, p.nombre, p.apellido, p.razon_social, 
               s.imei_equipo, s.falla_reportada, s.precio_final_ars, s.status 
        FROM servicios_reparacion s 
        JOIN personas p ON s.cliente_id = p.id 
        WHERE s.status = 'COMPLETADO' AND s.fecha_servicio BETWEEN ? AND ? 
        ORDER BY s.fecha_servicio DESC
    """
    reparaciones = db_query(query, (start_date, end_date_query))
    
    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID', 'Fecha', 'Cliente', 'IMEI/Equipo', 'Falla', 'Precio Final ARS', 'Estado'])
    
    for r in reparaciones:
        cliente = r['razon_social'] or f"{r['nombre']} {r['apellido']}"
        writer.writerow([
            r['id'], 
            r['fecha_servicio'], 
            cliente, 
            r['imei_equipo'], 
            r['falla_reportada'], 
            f"{r['precio_final_ars']:.2f}", 
            r['status']
        ])
    
    output.seek(0)
    return Response(
        output, 
        mimetype="text/csv", 
        headers={"Content-Disposition": f"attachment;filename=reporte_reparaciones_{start_date}.csv"}
    )


@app.route('/exportar/personas')
@login_required
def exportar_personas():
    # Obtener los mismos filtros de la vista listar_personas
    filtro_nombre = request.args.get('nombre', '').strip()
    filtro_cuit = request.args.get('cuit_cuil', '').strip()
    filtro_es_cliente = request.args.get('es_cliente') == '1'
    filtro_es_proveedor = request.args.get('es_proveedor') == '1'

    query = "SELECT * FROM personas WHERE 1=1"
    params = []

    if filtro_nombre:
        query += " AND (nombre LIKE ? OR apellido LIKE ? OR razon_social LIKE ?)"
        params.extend([f"%{filtro_nombre}%", f"%{filtro_nombre}%", f"%{filtro_nombre}%"])
    if filtro_cuit:
        query += " AND cuit_cuil LIKE ?"
        params.append(f"%{filtro_cuit}%")
    if filtro_es_cliente:
        query += " AND es_cliente = 1"
    if filtro_es_proveedor:
        query += " AND es_proveedor = 1"
    
    query += " ORDER BY razon_social, apellido, nombre"
    personas = db_query(query, tuple(params))

    output = io.StringIO()
    output.write('\ufeff') # BOM para Excel (tildes)
    writer = csv.writer(output, delimiter=';')
    
    # Encabezados
    writer.writerow(['ID', 'Razón Social', 'Nombre', 'Apellido', 'CUIT/CUIL', 'Teléfono', 'Email', '¿Es Cliente?', '¿Es Proveedor?'])
    
    for p in personas:
        writer.writerow([
            p['id'],
            p['razon_social'] or '',
            p['nombre'] or '',
            p['apellido'] or '',
            p['cuit_cuil'],
            p['telefono'] or '',
            p['email'] or '',
            'SÍ' if p['es_cliente'] else 'NO',
            'SÍ' if p['es_proveedor'] else 'NO'
        ])
    
    output.seek(0)
    fecha = datetime.now().strftime('%Y-%m-%d')
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=listado_personas_{fecha}.csv"}
    )



# =================================================================
# === MÓDULO DE CUENTAS VIRTUALES (BANCO / MERCADO PAGO) ==========
# =================================================================

@app.route('/cuentas/virtuales', methods=['GET', 'POST'])
@login_required
@admin_required  # Primero verificamos que sea al menos admin o superadmin
def gestion_cuentas_virtuales():
    # 1. Obtener filtros de fecha y el filtro de cuenta
    start_date, end_date_display, end_date_query = get_date_filters()
    filtro_cuenta = request.args.get('cuenta_filter', 'TODAS') # 'TODAS', 'BANCO' o 'MERCADO_PAGO'
    
    # 2. Calcular Saldo Actual de Banco (ARS y USD)
    # Calculamos ingresos y egresos en una sola pasada para mayor eficiencia
    res_banco = db_query("""
        SELECT 
            SUM(CASE WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' THEN monto_ars ELSE 0 END) AS ing_ars,
            SUM(CASE WHEN tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%' THEN monto_ars ELSE 0 END) AS egr_ars,
            SUM(CASE WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' THEN monto_usd ELSE 0 END) AS ing_usd,
            SUM(CASE WHEN tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%' THEN monto_usd ELSE 0 END) AS egr_usd
        FROM caja_movimientos 
        WHERE metodo_pago = 'BANCO'
    """)[0]
    
    saldo_banco_ars = (res_banco['ing_ars'] or 0.0) - (res_banco['egr_ars'] or 0.0)
    saldo_banco_usd = (res_banco['ing_usd'] or 0.0) - (res_banco['egr_usd'] or 0.0)

    # 3. Calcular Saldo Actual de Mercado Pago (ARS y USD)
    res_mp = db_query("""
        SELECT 
            SUM(CASE WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' THEN monto_ars ELSE 0 END) AS ing_ars,
            SUM(CASE WHEN tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%' THEN monto_ars ELSE 0 END) AS egr_ars,
            SUM(CASE WHEN tipo LIKE 'INGRESO%' OR tipo LIKE 'APERTURA%' THEN monto_usd ELSE 0 END) AS ing_usd,
            SUM(CASE WHEN tipo LIKE 'EGRESO%' OR tipo LIKE 'PAGO_PROVEEDOR%' OR tipo LIKE 'CIERRE%' THEN monto_usd ELSE 0 END) AS egr_usd
        FROM caja_movimientos 
        WHERE metodo_pago = 'MERCADO_PAGO'
    """)[0]
    
    saldo_mp_ars = (res_mp['ing_ars'] or 0.0) - (res_mp['egr_ars'] or 0.0)
    saldo_mp_usd = (res_mp['ing_usd'] or 0.0) - (res_mp['egr_usd'] or 0.0)

    # 4. Procesar movimientos manuales (POST)
    if request.method == 'POST':
        tipo = request.form.get('tipo') # INGRESO_MANUAL / EGRESO_MANUAL
        monto_ars = float(request.form.get('monto_ars', 0) or 0)
        monto_usd = float(request.form.get('monto_usd', 0) or 0)
        cuenta = request.form.get('cuenta') # BANCO / MERCADO_PAGO
        descripcion = request.form.get('descripcion')

        if monto_ars > 0 or monto_usd > 0:
            registrar_movimiento_caja(
                current_user.id, 
                tipo + "_VIRTUAL", 
                monto_ars=monto_ars, 
                monto_usd=monto_usd, # Ahora pasamos también el monto en dólares
                descripcion=descripcion, 
                metodo_pago=cuenta
            )
            flash(f"Movimiento en {cuenta} registrado correctamente.", "success")
            return redirect(url_for('gestion_cuentas_virtuales'))
        else:
            flash("Debe ingresar un monto mayor a cero en ARS o USD.", "danger")

    # 5. Listado de movimientos con filtros de FECHA y CUENTA aplicados
    query = """
        SELECT cm.*, u.username FROM caja_movimientos cm 
        JOIN users u ON cm.user_id = u.id 
        WHERE cm.fecha BETWEEN ? AND ?
    """
    params = [start_date, end_date_query]

    # Aplicamos filtro de cuenta si no es "TODAS"
    if filtro_cuenta == 'TODAS':
        query += " AND cm.metodo_pago IN ('BANCO', 'MERCADO_PAGO')"
    else:
        query += " AND cm.metodo_pago = ?"
        params.append(filtro_cuenta)

    query += " ORDER BY cm.fecha DESC"
    movimientos = db_query(query, tuple(params))

    return render_template('caja/cuentas_virtuales.html', 
                           movimientos=movimientos,
                           saldo_banco_ars=saldo_banco_ars,
                           saldo_banco_usd=saldo_banco_usd,
                           saldo_mp_ars=saldo_mp_ars,
                           saldo_mp_usd=saldo_mp_usd,
                           start_date=start_date,
                           end_date=end_date_display,
                           filtro_cuenta=filtro_cuenta)



# =================================================================
# === MÓDULO SUPERADMIN: AJUSTES E INVENTARIO INICIAL =============
# =================================================================

@app.route('/admin/inventario/ajuste')
@login_required
@superadmin_required
def menu_ajuste_stock():
    # Obtenemos categorías para los filtros
    categorias = db_query("SELECT DISTINCT categoria FROM repuestos")
    return render_template('admin/ajuste_stock_menu.html', categorias=categorias)

@app.route('/admin/inventario/ajuste/equipos', methods=['GET', 'POST'])
@login_required
@superadmin_required
def ajuste_stock_equipos():
    if request.method == 'POST':
        accion = request.form.get('accion') 
        
        if accion == 'CARGA_INICIAL':
            # Los mismos tipos que en registrar_compra_celular
            tipo_item = request.form.get('tipo_item') 
            marca = request.form.get('marca').upper().strip()
            modelo = request.form.get('modelo').upper().strip()
            imei = request.form.get('imei').strip()
            # La misma condición que en registrar_compra_celular
            condicion = request.form.get('condicion')
            color = request.form.get('color').strip()
            almacenamiento = int(request.form.get('almacenamiento') or 0)
            ram = int(request.form.get('ram') or 0)
            bateria = request.form.get('bateria')
            costo_usd = float(request.form.get('costo_usd', 0))
            obs_usuario = request.form.get('observaciones', '').strip()
            
            # Formateamos observaciones para mantener consistencia con el sistema
            observaciones_final = f"[{tipo_item}] {obs_usuario}".strip()

            db_conn = get_db()
            try:
                # Verificación de IMEI Duplicado
                existe = db_query("SELECT id FROM celulares WHERE imei = ?", (imei,))
                if existe:
                    flash(f"Error: El IMEI/SN {imei} ya está registrado en el sistema.", "danger")
                else:
                    # Inserción en la tabla celulares
                    cel_id = db_execute("""
                        INSERT INTO celulares 
                        (marca, modelo, imei, condicion, almacenamiento_gb, ram_gb, color, bateria_salud, costo_usd, stock, observaciones, es_parte_pago) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0)""",
                        (marca, modelo, imei, condicion, almacenamiento, ram, color, bateria, costo_usd, observaciones_final), 
                        return_id=True
                    )
                    
                    registrar_movimiento(current_user.id, 'AJUSTE_CARGA_INICIAL', 'CELULAR', cel_id, {
                        'tipo': tipo_item,
                        'imei': imei,
                        'condicion': condicion,
                        'costo': costo_usd
                    })
                    flash(f"Carga Inicial Exitosa: {marca} {modelo} ({condicion}).", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")

        elif accion == 'DESCUENTO_DIFERENCIA':
            imei = request.form.get('imei').strip()
            motivo = request.form.get('motivo', 'Ajuste SuperAdmin')
            equipo = db_query("SELECT id FROM celulares WHERE imei = ? AND stock = 1", (imei,))
            if not equipo:
                flash("No existe ese IMEI disponible para descontar.", "danger")
            else:
                db_execute("UPDATE celulares SET stock = 0, observaciones = observaciones || ? WHERE imei = ?", 
                           (f" | BAJA POR AJUSTE: {motivo}", imei))
                registrar_movimiento(current_user.id, 'AJUSTE_DESCUENTO', 'CELULAR', equipo[0]['id'], {'imei': imei, 'motivo': motivo})
                flash(f"Equipo {imei} descontado del stock.", "warning")

        return redirect(url_for('ajuste_stock_equipos'))

    return render_template('admin/ajuste_equipos.html')


@app.route('/admin/inventario/ajuste/insumos', methods=['GET', 'POST'])
@login_required
@superadmin_required
def ajuste_stock_insumos():
    if request.method == 'POST':
        repuesto_id = request.form.get('repuesto_id')
        tipo_ajuste = request.form.get('tipo_ajuste') # 'SUMAR' o 'RESTAR' o 'SET'
        cantidad = int(request.form.get('cantidad', 0))
        motivo = request.form.get('motivo', 'Ajuste manual SuperAdmin')

        repuesto = db_query("SELECT stock, nombre_parte, categoria FROM repuestos WHERE id = ?", (repuesto_id,))[0]
        stock_actual = repuesto['stock']

        if tipo_ajuste == 'SUMAR':
            nuevo_stock = stock_actual + cantidad
        elif tipo_ajuste == 'RESTAR':
            nuevo_stock = max(0, stock_actual - cantidad)
        else: # SET (Sobrescribir)
            nuevo_stock = cantidad

        db_execute("UPDATE repuestos SET stock = ? WHERE id = ?", (nuevo_stock, repuesto_id))
        
        registrar_movimiento(current_user.id, 'AJUSTE_STOCK_INSUMO', 'REPUESTO', repuesto_id, {
            'item': repuesto['nombre_parte'],
            'stock_anterior': stock_actual,
            'nuevo_stock': nuevo_stock,
            'motivo': motivo
        })
        
        flash(f"Stock de {repuesto['nombre_parte']} actualizado a {nuevo_stock}.", "success")
        return redirect(url_for('ajuste_stock_insumos'))

    # Para el GET, enviamos todos los repuestos y accesorios
    items = db_query("SELECT id, nombre_parte, modelo_compatible, stock, categoria FROM repuestos ORDER BY categoria, nombre_parte")
    return render_template('admin/ajuste_insumos.html', items=items)

# Ruta rápida para crear el ítem de repuesto/accesorio si no existe en la carga inicial
@app.route('/admin/inventario/crear_item_base', methods=['POST'])
@login_required
@superadmin_required
def crear_item_base():
    nombre = request.form.get('nombre').upper().strip()
    modelo = request.form.get('modelo').upper().strip() or 'Universal'
    categoria = request.form.get('categoria') # 'REPUESTO', 'ACCESORIO', 'OTROS'
    costo = float(request.form.get('costo_usd', 0))
    stock_inicial = int(request.form.get('stock_inicial', 0))

    try:
        # Verificamos si ya existe la combinación nombre+modelo+categoria
        existe = db_query("SELECT id FROM repuestos WHERE nombre_parte = ? AND modelo_compatible = ? AND categoria = ?", 
                         (nombre, modelo, categoria))
        
        if existe:
            flash(f"El ítem '{nombre}' para '{modelo}' ya existe. Use la tabla de ajustes para modificar su stock.", "warning")
        else:
            rep_id = db_execute(
                "INSERT INTO repuestos (nombre_parte, modelo_compatible, categoria, costo_usd, stock) VALUES (?, ?, ?, ?, ?)",
                (nombre, modelo, categoria, costo, stock_inicial), return_id=True
            )
            
            registrar_movimiento(current_user.id, 'CARGA_INICIAL_SISTEMA', 'PRODUCTO', rep_id, {
                'nombre': nombre,
                'categoria': categoria,
                'stock_inicial': stock_inicial,
                'costo_usd': costo
            })
            
            flash(f"Carga inicial de '{nombre}' ({stock_inicial} unidades) completada con éxito.", "success")
            
    except Exception as e:
        flash(f"Error: {e}", "danger")
    
    return redirect(url_for('ajuste_stock_insumos'))



def ejecutar_migraciones_y_configuracion():
    """
    Esta función contiene toda la lógica de creación de tablas y 
    migraciones. Se ejecuta cada vez que la app inicia.
    """
    inicializar_db()
    with app.app_context():
        db_conn = get_db()
        
        # ==========================================
        # 1. CREACIÓN DE TABLAS NUEVAS (IF NOT EXISTS)
        # ==========================================
        
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS items_promocionales_venta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id INTEGER NOT NULL,
                repuesto_id INTEGER NOT NULL,
                cantidad INTEGER DEFAULT 1,
                costo_usd_momento REAL DEFAULT 0.0,
                FOREIGN KEY (venta_id) REFERENCES ventas(id),
                FOREIGN KEY (repuesto_id) REFERENCES repuestos(id)
            )
        """)
        
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS cobros_clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL,
                user_id INTEGER,
                fecha_cobro DATETIME,
                monto_ars REAL DEFAULT 0.0,
                monto_usd REAL DEFAULT 0.0,
                metodo_pago TEXT, 
                referencia TEXT,
                observaciones TEXT,
                imputacion TEXT DEFAULT 'EQUIPOS',
                FOREIGN KEY (cliente_id) REFERENCES personas(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS ventas_cuotas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id INTEGER NOT NULL,
                numero_cuota INTEGER NOT NULL,
                monto_ars REAL NOT NULL,
                fecha_vencimiento DATE NOT NULL,
                estado TEXT DEFAULT 'PENDIENTE',
                FOREIGN KEY (venta_id) REFERENCES ventas(id)
            )
        """)

        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS cuentas_entidades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE,
                titular TEXT,
                activo INTEGER DEFAULT 1
            )
        """)
        
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS items_adicionales_venta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                venta_id INTEGER NOT NULL,
                repuesto_id INTEGER NOT NULL,
                cantidad INTEGER DEFAULT 1,
                precio_vendido_usd REAL,
                costo_usd_momento REAL,
                FOREIGN KEY (venta_id) REFERENCES ventas(id),
                FOREIGN KEY (repuesto_id) REFERENCES repuestos(id)
            )
        """)

        # ==========================================
        # 2. MIGRACIONES DE COLUMNAS (ALTER TABLE)
        # ==========================================
        
        def agregar_columna(tabla, columna, definicion):
            try:
                db_conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
            except sqlite3.OperationalError:
                pass 

        agregar_columna("celulares", "es_parte_pago", "BOOLEAN DEFAULT 0")
        agregar_columna("ventas", "celular_parte_pago_id", "INTEGER")
        agregar_columna("ventas", "valor_celular_parte_pago", "REAL")
        agregar_columna("ventas", "monto_transferencia_ars", "REAL DEFAULT 0.0")
        agregar_columna("ventas", "monto_debito_ars", "REAL DEFAULT 0.0")
        agregar_columna("ventas", "monto_credito_ars", "REAL DEFAULT 0.0")
        agregar_columna("ventas", "monto_mp_ars", "REAL DEFAULT 0.0")
        agregar_columna("ventas", "saldo_pendiente", "REAL DEFAULT 0.0")
        agregar_columna("ventas", "cantidad_cuotas", "INTEGER DEFAULT 1")
        agregar_columna("ventas", "observaciones", "TEXT")
        agregar_columna("ventas", "monto_virtual_usd", "REAL DEFAULT 0.0") # <--- AÑADE ESTA LÍNEA
        agregar_columna("ventas_cuotas", "monto_original_ars", "REAL")
        agregar_columna("servicios_reparacion", "tipo_servicio", "TEXT DEFAULT 'REPARACION'")
        agregar_columna("servicios_reparacion", "saldo_pendiente", "REAL DEFAULT 0.0")
        agregar_columna("servicios_reparacion", "tecnico_id", "INTEGER")
        agregar_columna("servicios_reparacion", "tecnico_nombre", "TEXT")
        agregar_columna("servicios_reparacion", "fecha_pago_tecnico", "DATETIME")
        agregar_columna("servicios_reparacion", "pago_tecnico_estado", "TEXT DEFAULT 'PENDIENTE'")
        agregar_columna("servicios_reparacion", "comision_pct", "REAL DEFAULT 0.0")
        agregar_columna("servicios_reparacion", "comision_pagada_ars", "REAL DEFAULT 0.0")
        agregar_columna("repuestos", "precio_venta_ars", "REAL DEFAULT 0.0")
        agregar_columna("repuestos", "precio_venta_usd", "REAL DEFAULT 0.0")
        agregar_columna("repuestos", "categoria", "TEXT DEFAULT 'REPUESTO'")
        agregar_columna("caja_movimientos", "sub_categoria", "TEXT")
        agregar_columna("caja_movimientos", "metodo_pago", "TEXT DEFAULT 'EFECTIVO'")
        agregar_columna("pagos_proveedores", "valor_dolar_momento", "REAL DEFAULT 1.0")
        agregar_columna("pagos_proveedores", "imputacion", "TEXT DEFAULT 'EQUIPOS'")
        agregar_columna("personas", "fecha_nacimiento", "DATE")
        agregar_columna("users", "active", "INTEGER DEFAULT 1")
        agregar_columna("repuestos_usados", "manual_item_nombre", "TEXT")

        # ==========================================
        # 3. MIGRACIÓN DEFINITIVA: RECREAR REPUESTOS_USADOS
        # ==========================================
        try:
            db_conn.execute("PRAGMA foreign_keys = OFF")
            cursor = db_conn.execute("PRAGMA table_info(repuestos_usados)")
            columnas = cursor.fetchall()
            repuesto_id_info = next((c for c in columnas if c['name'] == 'repuesto_id'), None)
            
            if repuesto_id_info and repuesto_id_info['notnull'] == 1:
                db_conn.execute("ALTER TABLE repuestos_usados RENAME TO repuestos_usados_old")
                db_conn.execute("""
                    CREATE TABLE repuestos_usados (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        servicio_id INTEGER NOT NULL,
                        repuesto_id INTEGER,
                        manual_item_nombre TEXT,
                        cantidad INTEGER NOT NULL,
                        costo_usd_momento REAL NOT NULL,
                        FOREIGN KEY (servicio_id) REFERENCES servicios_reparacion(id),
                        FOREIGN KEY (repuesto_id) REFERENCES repuestos(id)
                    )
                """)
                db_conn.execute("""
                    INSERT INTO repuestos_usados (id, servicio_id, repuesto_id, manual_item_nombre, cantidad, costo_usd_momento)
                    SELECT id, servicio_id, repuesto_id, NULL, cantidad, costo_usd_momento FROM repuestos_usados_old
                """)
                db_conn.execute("DROP TABLE repuestos_usados_old")
        except Exception as e:
            app.logger.error(f"Error crítico en migración: {e}")
        finally:
            db_conn.execute("PRAGMA foreign_keys = ON")
                
        # ==========================================
        # 4. DATOS INICIALES Y SEEDING
        # ==========================================
        db_conn.execute("INSERT OR IGNORE INTO cuentas_entidades (nombre, titular) VALUES ('BANCO', 'MY POINT')")
        db_conn.execute("INSERT OR IGNORE INTO cuentas_entidades (nombre, titular) VALUES ('MERCADO_PAGO', 'MY POINT')")

        if not db_query("SELECT id FROM users WHERE username = 'superadmin'"):
            hashed_pw = generate_password_hash('superadmin', method='pbkdf2:sha256')
            db_execute("INSERT INTO users (username, password, role, active) VALUES (?, ?, ?, 1)", 
                      ('superadmin', hashed_pw, 'superadmin'))
        
        db_conn.commit()

# --- EJECUCIÓN INICIAL ---
# Llamamos a la función aquí para que corra tanto en local como en el servidor
ejecutar_migraciones_y_configuracion()

# --- BLOQUE DE DESARROLLO LOCAL ---
if __name__ == '__main__':
    # Detectamos si estamos en producción (esto lo configurarás en el servidor)
    es_produccion = os.environ.get('FLASK_ENV') == 'production'
    
    app.run(
        host='0.0.0.0', 
        port=5000, 
        debug=not es_produccion
    )