from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.index, name='index'),
    path('robots.txt', views.robots_txt, name='robots_txt'),
    path('sitemap.xml', views.sitemap_xml, name='sitemap_xml'),
    path('.well-known/security.txt', views.security_txt, name='security_txt'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('healthz', views.healthz, name='healthz'),
    path('room/<str:room_name>/', views.room, name='room'),
    path('room/<str:room_name>/messages/', views.room_messages, name='room_messages'),
    path('room/<str:room_name>/search/', views.room_search, name='room_search'),
    path('room/<str:room_name>/invite/', views.room_invite, name='room_invite'),
    path(
        'room/<str:room_name>/invite/revoke/',
        views.room_revoke_invites,
        name='room_revoke_invites',
    ),
    path('room/<str:room_name>/kick/', views.room_kick_member, name='room_kick_member'),
    path('room/<str:room_name>/update/', views.room_update, name='room_update'),
    path('room/<str:room_name>/export/', views.room_export, name='room_export'),
    path('room/<str:room_name>/delete/', views.room_delete, name='room_delete'),
    path('join/<str:token>/', views.room_join, name='room_join'),
    path('create/', views.create_room, name='create_room'),
    path('dm/<str:username>/', views.start_dm, name='start_dm'),
    path('users/<str:username>/profile/', views.user_profile_json, name='user_profile_json'),
    path('ice-servers/', views.ice_servers, name='ice_servers'),
    path('register/', views.register, name='register'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('account/profile/', views.profile_view, name='profile'),
    path('account/export/', views.account_export, name='account_export'),
    path('account/password/', views.CustomPasswordChangeView.as_view(), name='password_change'),
    path('account/delete/', views.delete_account, name='delete_account'),
]
