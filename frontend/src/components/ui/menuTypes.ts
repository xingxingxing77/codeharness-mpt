export interface MenuItem {
  key?: string
  label?: string
  icon?: string
  danger?: boolean
  checked?: boolean
  disabled?: boolean
  /** 分组标题 / 分隔线，默认可点行 */
  kind?: 'item' | 'label' | 'sep'
  /** 选中后菜单保持展开（勾选型多选菜单用） */
  keepOpen?: boolean
}
