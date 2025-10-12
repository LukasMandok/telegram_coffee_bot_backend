"""
Group state initialization helpers for the Telegram coffee bot.

This module provides utilities to initialize the GroupState from database users
instead of using hardcoded member lists.
"""

from typing import Dict
from ..dependencies.dependencies import get_repo
from .telethon_models import GroupState, GroupMember

# TODO: implement log states here


async def initialize_group_state_from_db() -> GroupState:
    """
    Initialize a GroupState by loading all users from the database.
    
    Creates a GroupState with all TelegramUser and PassiveUser display names
    as members, each starting with 0 coffee orders.
    
    Returns:
        GroupState: Initialized group state with database users
    """
    repo = get_repo()
    all_users = await repo.find_all_users() or []
    
    # Create members dictionary with display_name as key and GroupMember as value
    members = {}
    for user in all_users:
        if hasattr(user, 'display_name') and user.display_name:
            user_id = user.user_id if hasattr(user, 'user_id') else None
            members[user.display_name] = GroupMember(name=user.display_name, user_id=user_id, coffee_count=0)
        else:
            raise ValueError(f"User {user.id} has no display name")
    
    print(f"[GROUP STATE] Initialized with {len(members)} users from database")
    for name in sorted(members.keys()):
        print(f"  - {name}")
    
    return GroupState(members=members)


# TODO: this seems overcomplicated. Is this even necessary? And can this be implemented in way less lines and loops?
async def refresh_group_state_members(group_state: GroupState) -> GroupState:
    """
    Refresh the members in an existing GroupState from the database.
    
    This preserves existing coffee counts for users that still exist,
    adds new users with 0 coffees, and removes users that no longer exist.
    
    Args:
        group_state: Existing GroupState to refresh
        
    Returns:
        GroupState: Updated group state with current database users
    """
    repo = get_repo()
    all_users = await repo.find_all_users() or []
    
    # Get current database user display names
    current_db_users = set()
    for user in all_users:
        if hasattr(user, 'display_name') and user.display_name:
            current_db_users.add(user.display_name)
    
    # Preserve existing coffee counts for users that still exist
    new_members = {}
    for display_name in current_db_users:
        if display_name in group_state.members:
            # Keep existing GroupMember object
            new_members[display_name] = group_state.members[display_name]
        else:
            # New user, create GroupMember with 0 coffees
            new_members[display_name] = GroupMember(name=display_name, user_id=None, coffee_count=0)
    
    # Update the group state
    old_count = len(group_state.members)
    group_state.members = new_members
    new_count = len(new_members)
    
    print(f"🔄 [GROUP STATE] Refreshed members: {old_count} -> {new_count} users")
    
    return group_state


def get_users_with_orders(group_state: GroupState) -> Dict[str, int]:
    """
    Get only the users who have coffee orders (count > 0).
    
    Args:
        group_state: GroupState to filter
        
    Returns:
        Dict[str, int]: Dictionary of display_name -> coffee_count for users with orders
    """
    return {name: member_data.coffee_count for name, member_data in group_state.members.items() if member_data.coffee_count > 0}


def get_group_summary(group_state: GroupState) -> str:
    """
    Generate a human-readable summary of the group state.
    
    Args:
        group_state: GroupState to summarize
        
    Returns:
        str: Formatted summary string
    """
    total_coffees = group_state.get_total_coffees()
    total_members = len(group_state.members)
    members_with_orders = len(get_users_with_orders(group_state))
    
    summary = f"Group Summary:\n"
    summary += f"  Total Members: {total_members}\n"
    summary += f"  Members with Orders: {members_with_orders}\n"
    summary += f"  Total Coffees: {total_coffees}\n"
    
    if members_with_orders > 0:
        summary += f"  Orders:\n"
        for name, count in get_users_with_orders(group_state).items():
            summary += f"    {name}: {count}\n"
    
    return summary