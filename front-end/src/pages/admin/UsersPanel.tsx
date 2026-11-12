import type { Dispatch, FormEvent, SetStateAction } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { DataTable } from '../../components/DataTable';
import type { ApprovalGroup, User } from './types';

const roles = ['reader', 'designer', 'reviewer', 'administrator'];

export type UsersPanelProps = {
  users: User[];
  filteredUsers: User[];
  approvalGroups: ApprovalGroup[];
  columns: ColumnDef<User, any>[];
  loading: boolean;
  busy: boolean;
  search: string;
  roleFilter: string;
  statusFilter: string;
  groupFilter: string;
  setSearch: Dispatch<SetStateAction<string>>;
  setRoleFilter: Dispatch<SetStateAction<string>>;
  setStatusFilter: Dispatch<SetStateAction<string>>;
  setGroupFilter: Dispatch<SetStateAction<string>>;
  setSelectedUser: Dispatch<SetStateAction<User | null>>;
  setShowCreateUser: Dispatch<SetStateAction<boolean>>;
  onRefresh: () => Promise<void>;
  onCreateGroup: (event: FormEvent<HTMLFormElement>) => void;
  onToggleGroup: (group: ApprovalGroup) => Promise<void>;
};

export function UsersPanel(props: UsersPanelProps) {
  const {
    users,
    filteredUsers,
    approvalGroups,
    columns,
    loading,
    busy,
    search,
    roleFilter,
    statusFilter,
    groupFilter,
    setSearch,
    setRoleFilter,
    setStatusFilter,
    setGroupFilter,
    setShowCreateUser,
    onRefresh,
    onCreateGroup,
    onToggleGroup,
  } = props;
  return (
    <div className="admin-tab-panel">
      <div
        className="admin-section"
        style={{
          display: 'flex',
          flexDirection: 'column',
          padding: 0,
          gap: '0.5rem',
          height: '100%',
        }}
      >
        <div className="admin-user-heading">
          <div>
            <h2>User management</h2>
            <p>Manage identities, access roles, approval responsibilities, and account status.</p>
          </div>
          <div>
            <button type="button" onClick={() => setShowCreateUser(true)}>
              Add user
            </button>
            <button type="button" onClick={() => void onRefresh()}>
              Refresh
            </button>
          </div>
        </div>
        <div className="admin-user-filters" role="search" aria-label="Filter users">
          <label>
            Search
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Name or username"
            />
          </label>
          <label>
            Role
            <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
              <option value="">All roles</option>
              {roles.map((role) => (
                <option key={role}>{role}</option>
              ))}
            </select>
          </label>
          <label>
            Status
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="locked">Locked</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <label>
            Approval group
            <select value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
              <option value="">All groups</option>
              {approvalGroups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </label>
          <div className="admin-user-filter-actions">
            <span>
              {filteredUsers.length} of {users.length} users
            </span>
            <button
              type="button"
              disabled={!search && !roleFilter && !statusFilter && !groupFilter}
              onClick={() => {
                setSearch('');
                setRoleFilter('');
                setStatusFilter('');
                setGroupFilter('');
              }}
            >
              Clear
            </button>
          </div>
        </div>
        <div style={{ flex: 1, minHeight: 0, padding: '0 1.25rem 1.25rem' }}>
          <DataTable
            columns={columns}
            data={filteredUsers}
            getRowId={(user) => user.id}
            loading={loading}
            emptyMessage={users.length ? 'No users match the filters' : 'No users'}
          />
        </div>
        <div style={{ padding: '0 1.25rem 1.25rem' }}>
          <h2>Reviewer groups</h2>
          <form className="admin-create-user" onSubmit={onCreateGroup}>
            <input name="groupName" placeholder="Group name" required />
            <select name="memberIds" multiple required aria-label="Reviewer group members">
              {users
                .filter(
                  (user) => user.is_active && ['reviewer', 'administrator'].includes(user.role)
                )
                .map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.display_name} ({user.role})
                  </option>
                ))}
            </select>
            <button disabled={busy}>Add group</button>
          </form>
          <div className="notification-list">
            {approvalGroups.map((group) => (
              <div className="notification-card" key={group.id}>
                <strong>{group.name}</strong>
                <span className="muted">
                  {' '}
                  {group.member_ids.length} member(s) · {group.is_active ? 'Active' : 'Inactive'}
                </span>
                <button type="button" onClick={() => void onToggleGroup(group)}>
                  {group.is_active ? 'Deactivate' : 'Activate'}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
