# Directivas del Agente y Estándares de Ingeniería de Software (Python / Next.js)

Este documento define las reglas estrictas de desarrollo para todos los agentes de IA que operen en este repositorio. Cualquier generación de código, refactorización o diseño arquitectónico debe alinearse sin excepciones con estos principios.

---

## 1. Backend: Python & FastAPI (POO y Arquitectura Limpia)
Aunque Python es multiparadigma, en este proyecto se exige un enfoque **Orientado a Objetos (POO)** estricto y tipado.

- **Tipado Estricto (Type Hinting):** Todo código en Python debe usar tipado estricto. Todas las funciones de clase deben declarar el tipo de sus argumentos y su retorno (`def ejecutar(self, id: str) -> UserDTO:`).
- **Validación con Pydantic v2:** Toda entrada y salida de datos en las rutas de FastAPI debe mapearse mediante modelos de clases de Pydantic. Queda prohibido recibir o retornar diccionarios (`dict`) genéricos.
- **Inyección de Dependencias:** Utilizar el sistema `Depends()` de FastAPI para inyectar instancias de clases de servicio y repositorios, asegurando el desacoplamiento.
- **Patrón Repositorio:** Toda interacción con la base de datos (**SQL Server** vía SQLAlchemy/SQLModel o **MongoDB** vía Beanie/Motor) debe estar encapsulada en una clase Repositorio que herede de una interfaz o clase abstracta. La lógica de negocio jamás debe saber qué base de datos se está usando.

## 2. Frontend: Next.js (React) & Tailwind CSS
El frontend debe ser modular, escalable y con un rendimiento optimizado.

- **Componentes de Servidor vs. Cliente (App Router):** Por defecto, todos los componentes deben ser Server Components (`RSC`). Solo usar `'use client'` en componentes que requieran interactividad (hooks como `useState`, `useEffect`) o eventos de usuario.
- **POO en Frontend (Services/Gateways):** Toda lógica de comunicación con la API de FastAPI debe estar encapsulada en clases de servicios/clientes HTTP en TypeScript/JavaScript, aplicando encapsulamiento para aislar los componentes visuales de los detalles de la red.
- **Estilos con Tailwind CSS:** Utilizar exclusivamente clases utilitarias de Tailwind. Evitar CSS tradicional o estilos inline. Mantener consistencia visual utilizando componentes reutilizables.
- **Estructura Atómica:** Separar componentes en elementos mínimos reutilizables (Botones, Inputs) e integrarlos en componentes complejos (Formularios, Tablas).

## 3. Estrategia de Pruebas Unitarias con Pytest (Obligatorio)
No se acepta código en el backend que no esté respaldado por pruebas automatizadas en `pytest`.

- **Aislamiento con Mocking:** Queda terminantemente prohibido que una prueba unitaria intente conectarse a una instancia real de SQL Server o MongoDB. Se debe utilizar `unittest.mock` (`MagicMock`, `AsyncMock`) o los fixtures de `pytest-mock` para simular las respuestas de los repositorios.
- **Pytest Fixtures:** Organizar la configuración de dependencias y datos de prueba comunes dentro de archivos `conftest.py` utilizando fixtures reutilizables y tipados.
- **Pruebas Asíncronas:** Dado que FastAPI maneja asincronía, las pruebas de rutas o servicios asíncronos deben utilizar `pytest-asyncio` correctamente decoradas con `@pytest.mark.asyncio`.
- **Estructura AAA (Arrange-Act-Assert):** Mantener las pruebas limpias y legibles separando la preparación de datos, la ejecución del método de la clase, y las aserciones finales.

## 4. Estilo de Código y Calidad General
- **Idioma:** Los nombres de clases, métodos, variables y mensajes de commit se escriben en **inglés**. Los comentarios y docstrings se escriben rigurosamente en **español**.
- **Clean Code en Python:** Cumplir con las directrices de PEP 8. Las clases deben tener una única responsabilidad (Single Responsibility Principle). Las funciones o métodos de clase no deben exceder las 25 líneas de código.
- **Manejo de Excepciones:** Crear excepciones personalizadas que hereden de `Exception` para controlar los errores de negocio (ej. `UserNotFoundError`). FastAPI debe capturar estas excepciones mediante manejadores globales (`exception_handlers`) para transformarlas en respuestas HTTP limpias, evitando llenar la lógica interna de bloques `try-catch` repetitivos.
- **Documentación de funciones:** Los docstrings de funciones y métodos se escriben entre triple comilla doble (`""" """`) explicando el propósito, argumentos y retorno.
- **Comentarios en línea:** Los comentarios línea por línea se redactan en **tercera persona** y describen la intención (ej. `# Intenta parsear la respuesta de la API`). Evitar comentarios en primera o segunda persona como `# Debes intentar parsear la respuesta` o `# Esto es lo que se debe hacer`.)

## 5. Despliegue con Docker y CI/CD
- **Docker:** Todas las aplicaciones se contenedorizan con Docker. Cada proyecto incluye un `Dockerfile` optimizado para producción.
- **deploy.yml integrado:** Cada repositorio contiene un pipeline de despliegue en `.github/workflows/deploy.yml` que se ejecuta sobre un **self-hosted runner**. El pipeline debe encargarse de construir la imagen, publicarla y desplegarla en el entorno correspondiente.

## 6. Artefactos Esenciales del Proyecto
- **Integridad obligatoria:** Todo proyecto debe crear o verificar la existencia e integridad de los siguientes archivos antes de darse por completo:
  - `.github/workflows/deploy.yml` — Pipeline de CI/CD.
  - `docker-compose.yml` — Orquestación de servicios para desarrollo y producción.
  - `.gitignore` — Exclusión de archivos locales, temporales y de herramientas.
  - `requirements.txt` (backend) / `package.json` (frontend) — Dependencias del proyecto.
- **Regla de validación:** Si alguno de estos archivos falta o está incompleto (ej. sin healthcheck, sin volúmenes, sin dependencias), el agente debe detenerse, reportar la omisión y crearlo o corregirlo antes de continuar.
