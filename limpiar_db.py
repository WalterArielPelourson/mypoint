import sqlite3
import os

# Configuración del nombre de la base de datos
DB_NAME = "negocio_erp.db"

def normalizar_datos():
    # Verificar si el archivo de la base de datos existe
    if not os.path.exists(DB_NAME):
        print(f"Error: No se encontró el archivo {DB_NAME}. Asegúrate de estar en la carpeta correcta.")
        return

    try:
        # Conexión a la base de datos
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        print("--- Iniciando proceso de limpieza y normalización ---")

        # 1. Convertir a minúsculas todos los Repuestos y Accesorios existentes
        print("Normalizando tabla 'repuestos'...")
        cursor.execute("""
            UPDATE repuestos 
            SET nombre_parte = LOWER(TRIM(nombre_parte)), 
                modelo_compatible = LOWER(TRIM(modelo_compatible))
        """)
        print(f"-> Repuestos actualizados: {cursor.rowcount}")

        # 2. Convertir a minúsculas los tipos de ítem en la tabla Compras
        print("Normalizando categorías en tabla 'compras'...")
        cursor.execute("""
            UPDATE compras 
            SET tipo_item = LOWER(TRIM(tipo_item)) 
            WHERE tipo_item IN ('REPUESTO', 'ACCESORIO', 'PRODUCTO', 'OTRO')
        """)
        print(f"-> Compras actualizadas: {cursor.rowcount}")

        # 3. Convertir nombres manuales en Repuestos Usados (para Servicios Técnicos)
        print("Normalizando nombres manuales en 'repuestos_usados'...")
        cursor.execute("""
            UPDATE repuestos_usados 
            SET manual_item_nombre = LOWER(TRIM(manual_item_nombre)) 
            WHERE manual_item_nombre IS NOT NULL
        """)
        print(f"-> Items manuales en servicios actualizados: {cursor.rowcount}")

        # 4. (Opcional) Eliminar espacios dobles accidentales
        # Esto ayuda si alguien escribió "funda  iphone" (con dos espacios)
        print("Limpiando espacios residuales...")
        cursor.execute("UPDATE repuestos SET nombre_parte = REPLACE(nombre_parte, '  ', ' ')")
        
        # Guardar cambios
        conn.commit()
        print("--- Proceso finalizado con éxito ---")
        print("Ahora todos tus productos y modelos están en minúsculas y sin espacios extra.")

    except sqlite3.Error as e:
        print(f"Error de base de datos: {e}")
    except Exception as e:
        print(f"Ocurrió un error inesperado: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # Confirmación de seguridad
    confirmacion = input("¿Estás seguro de que deseas convertir todos los nombres de repuestos a minúsculas? (s/n): ")
    if confirmacion.lower() == 's':
        normalizar_datos()
    else:
        print("Operación cancelada.")