import sqlite3

DB_FILE = "negocio_erp.db"

def inicializar_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # --- TABLAS DE PERSONAS (CLIENTES/PROVEEDORES) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            apellido TEXT,
            razon_social TEXT,
            cuit_cuil TEXT UNIQUE NOT NULL,
            telefono TEXT,
            email TEXT,
            es_cliente BOOLEAN NOT NULL DEFAULT 0,
            es_proveedor BOOLEAN NOT NULL DEFAULT 0
        )
    ''')

    # --- TABLAS DE INVENTARIO Y VENTAS ---
    # Celulares: Cada entrada es una unidad física única con su IMEI. Stock es 1 (disponible) o 0 (vendido).
    #-- MODIFICADO: Añadido 'es_parte_pago' para celulares recibidos como forma de pago
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS celulares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marca TEXT NOT NULL,
            modelo TEXT NOT NULL,
            imei TEXT UNIQUE NOT NULL, -- IMEI ahora es NOT NULL
            condicion TEXT NOT NULL,
            almacenamiento_gb INTEGER NOT NULL,
            ram_gb INTEGER,
            color TEXT NOT NULL,
            bateria_salud INTEGER NOT NULL, -- Solo para usados
            costo_usd REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 1, -- 1 para disponible, 0 para vendido/en parte de pago
            observaciones TEXT,
            es_parte_pago BOOLEAN DEFAULT 0 -- Indica si fue recibido como parte de pago y está pendiente de reingreso/valoración
        )
    ''')

    # --- TABLA DE VENTAS (MODIFICADA para opciones de ganancia y pagos) ---
    #-- Se añaden campos para el registro del pago real
    #-- 'ganancia_pct' ya no es NOT NULL, porque puede ser nula si se usa monto_agregado.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            celular_id INTEGER NOT NULL,
            cliente_id INTEGER NOT NULL,
            fecha_venta TEXT NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 1, -- Siempre 1 por cada celular_id
            valor_dolar_momento REAL NOT NULL, -- Valor del dólar de VENTA al momento de la cotización/venta
            impuestos_pct REAL NOT NULL,
            ganancia_pct REAL, -- Eliminado NOT NULL. Será el porcentaje si se usa, o NULL
            monto_agregado_ars REAL, -- Monto fijo en ARS agregado al costo (NULL si se usa ganancia_pct/monto_usd)
            monto_agregado_usd REAL, -- Monto fijo en USD agregado al costo (NULL si se usa ganancia_pct/monto_ars)
            precio_final_ars REAL NOT NULL,
            precio_final_usd REAL NOT NULL,
            -- NUEVOS CAMPOS PARA REGISTRAR EL PAGO REAL
            monto_cobrado_ars REAL DEFAULT 0,
            monto_cobrado_usd REAL DEFAULT 0,
            celular_parte_pago_id INTEGER, -- ID del celular tomado como parte de pago
            valor_celular_parte_pago REAL DEFAULT 0, -- Valor asignado al celular tomado
            -- FIN NUEVOS CAMPOS
            status TEXT NOT NULL DEFAULT 'PRESUPUESTO', -- 'PRESUPUESTO', 'COMPLETADA', 'CANCELADA'
            FOREIGN KEY (celular_id) REFERENCES celulares (id),
            FOREIGN KEY (cliente_id) REFERENCES personas (id),
            FOREIGN KEY (celular_parte_pago_id) REFERENCES celulares (id) -- Referencia al celular tomado
        )
    ''')
    
    # --- TABLAS DE SERVICIO TÉCNICO ---
    # MODIFICACIÓN AQUÍ: Se añaden las columnas precio_venta_ars y precio_venta_usd
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS repuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_parte TEXT NOT NULL,
            modelo_compatible TEXT,
            costo_usd REAL NOT NULL, -- Costo promedio ponderado
            stock INTEGER NOT NULL,
            precio_venta_ars REAL DEFAULT 0.0, -- Añadido
            precio_venta_usd REAL DEFAULT 0.0, -- Añadido
            UNIQUE(nombre_parte, modelo_compatible)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servicios_reparacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            imei_equipo TEXT,
            falla_reportada TEXT NOT NULL,
            solucion_aplicada TEXT,
            costo_total_repuestos_usd REAL NOT NULL,
            precio_mano_obra_ars REAL NOT NULL,
            precio_final_ars REAL NOT NULL,
            fecha_servicio TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PRESUPUESTO', -- 'PRESUPUESTO', 'COMPLETADO', 'CANCELADO'
            FOREIGN KEY (cliente_id) REFERENCES personas (id)
        )
    ''')
    # Nota: la migración de 'repuesto_id' a NULLABLE y 'manual_item_nombre'
    # se sigue manejando en app.py porque requiere alterar la tabla y sus FKs.
    # Aquí en database.py se mantiene la definición inicial (que luego será alterada).
    # Si quisieras que el esquema de database.py ya reflejara el repuesto_id NULLABLE
    # y manual_item_nombre desde el inicio, deberías cambiarlo aquí también.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS repuestos_usados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servicio_id INTEGER NOT NULL,
            repuesto_id INTEGER NOT NULL, -- Este se hace NULLABLE en la migración de app.py
            cantidad INTEGER NOT NULL,
            costo_usd_momento REAL NOT NULL, -- Costo del repuesto al momento de usarlo
            FOREIGN KEY (servicio_id) REFERENCES servicios_reparacion (id) ON DELETE CASCADE,
            FOREIGN KEY (repuesto_id) REFERENCES repuestos (id)
        )
    ''')

    # --- TABLAS DE SISTEMA (USUARIOS Y AUDITORÍA) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL, 
            role TEXT NOT NULL DEFAULT 'vendedor'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id INTEGER,
            tipo_item TEXT,
            tipo_movimiento TEXT NOT NULL,
            fecha TEXT NOT NULL,
            detalles TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # --- NUEVAS TABLAS PARA CAJA Y ARQUEO (Ahora manejan ARS y USD explícitamente) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS caja_movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL, -- 'INGRESO_MANUAL_ARS', 'EGRESO_MANUAL_ARS', 'INGRESO_MANUAL_USD', 'EGRESO_MANUAL_USD', 'INGRESO_VENTA', 'INGRESO_SERVICIO', 'APERTURA_CAJA_ARS', 'CIERRE_CAJA_ARS', 'APERTURA_CAJA_USD', 'CIERRE_CAJA_USD', 'PAGO_PROVEEDOR_ARS', 'PAGO_PROVEEDOR_USD'
            monto_ars REAL DEFAULT 0,
            monto_usd REAL DEFAULT 0,
            descripcion TEXT,
            referencia_id INTEGER, -- ID de venta, servicio, arqueo o pago_proveedor
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS arqueo_caja (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fecha_apertura TEXT NOT NULL,
            fecha_cierre TEXT,
            monto_inicial_ars REAL NOT NULL,
            monto_sistema_calculado_ars REAL, 
            monto_contado_fisico_ars REAL, 
            diferencia_ars REAL, 
            monto_inicial_usd REAL NOT NULL,
            monto_sistema_calculado_usd REAL,
            monto_contado_fisico_usd REAL,
            diferencia_usd REAL,
            observaciones TEXT,
            estado TEXT NOT NULL DEFAULT 'ABIERTO', -- 'ABIERTO', 'CERRADO'
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # --- NUEVAS TABLAS PARA COMPRAS Y PAGOS A PROVEEDORES (CTAS CORRIENTES) ---

    # Tabla de Compras
    #-- Se añade un campo para el IMEI para compras de celulares, aunque el item_id ya lo apunta.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS compras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            fecha_compra TEXT NOT NULL,
            tipo_item TEXT NOT NULL, -- 'CELULAR', 'REPUESTO'
            item_id INTEGER, -- ID del celular o repuesto comprado
            imei_celular TEXT, -- Nuevo campo para compras de celulares
            cantidad INTEGER NOT NULL,
            costo_unitario_usd REAL NOT NULL,
            costo_total_usd REAL NOT NULL,
            valor_dolar_momento REAL NOT NULL,
            costo_total_ars REAL NOT NULL,
            estado_pago TEXT NOT NULL DEFAULT 'PENDIENTE', -- 'PENDIENTE', 'PAGADO_PARCIAL', 'PAGADO_TOTAL'
            FOREIGN KEY (proveedor_id) REFERENCES personas (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # Tabla de Pagos a Proveedores
    #--# Se añade FOREIGN KEY a compras (opcional, si el pago es específico para una compra)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pagos_proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proveedor_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            fecha_pago TEXT NOT NULL,
            compra_id INTEGER, -- Permite vincular un pago a una compra específica (o NULL para pagos generales)
            monto_ars REAL DEFAULT 0, 
            monto_usd REAL DEFAULT 0, 
            tipo_pago TEXT NOT NULL, -- 'EFECTIVO_ARS', 'EFECTIVO_USD', 'TRANSFERENCIA', 'TARJETA_CREDITO', 'TARJETA_DEBITO', 'OTROS'
            referencia TEXT, -- Ej: N° de transferencia, de comprobante de tarjeta
            observaciones TEXT,
            FOREIGN KEY (proveedor_id) REFERENCES personas (id),
            FOREIGN KEY (compra_id) REFERENCES compras (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    conn.commit()
    conn.close()

# Funciones db_query y db_execute (sin cambios en su lógica)

def db_query(db_connection, query, params=()):
    """
    Ejecuta una consulta SELECT y devuelve los resultados como una lista de diccionarios (sqlite3.Row).
    Requiere que la conexión db_connection tenga row_factory configurado a sqlite3.Row.
    """
    cursor = db_connection.cursor()
    cursor.execute(query, params)
    return cursor.fetchall()

def db_execute(db_connection, query, params=(), return_id=False):
    """
    Ejecuta una consulta INSERT, UPDATE o DELETE.
    Opcionalmente, devuelve el lastrowid para inserciones.
    """
    cursor = db_connection.cursor()
    cursor.execute(query, params)
    last_id = cursor.lastrowid if return_id else None
    db_connection.commit() # Realiza el commit en la conexión
    if return_id:
        return last_id

