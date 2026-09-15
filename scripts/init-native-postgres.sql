-- 在 audit_network 数据库中以数据库所有者/管理员执行。
-- 脚本可重复执行，不创建或删除业务表。
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_app') THEN
    CREATE ROLE audit_app NOINHERIT LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_migrator') THEN
    CREATE ROLE audit_migrator NOINHERIT LOGIN;
  END IF;
END
$$;

-- 生产环境必须通过密码管理系统设置密码；此处只授予连接和 schema 使用权。
GRANT CONNECT ON DATABASE audit_network TO audit_app;
GRANT USAGE ON SCHEMA public TO audit_app;
GRANT CONNECT ON DATABASE audit_network TO audit_migrator;
GRANT USAGE ON SCHEMA public TO audit_migrator;
