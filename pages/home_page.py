import dash
import dash_table
import dash_core_components as dcc
import dash_html_components as html
import pandas as pd
import numpy as np
import os
import tasks
import boto3
import base64
import time

from datetime import datetime, timedelta
from celery.result import AsyncResult
from flask import redirect
from tasks import celery_app
from dash.dependencies import Input, Output, State
from app import app

######################################### HELPER FUNCTIONS #############################################################


#  this function computes the distance between two locations on the earths surface
def compute_great_circle_distance(lat_user, lon_user, lat_station, lon_station):

    lat1, lon1 = np.radians([np.float64(lat_user), np.float64(lon_user)])
    lat2, lon2 = np.radians([lat_station, lon_station])
    a = np.sin((lat2 - lat1) / 2.0) ** 2 + np.cos(lat1) * \
        np.cos(lat2) * np.sin((lon2 - lon1) / 2.0) ** 2
    earth_radius_km = 6371

    return earth_radius_km * 2 * np.arcsin(np.sqrt(a))


#  this function downloads a file from s3
def download_csv_s3(s3, filepath, bucket):

    obj = s3.get_object(Bucket=bucket, Key=filepath)
    df = pd.read_csv(obj['Body'], index_col=0)

    return df

######################################### DATA INPUTS AND LINKS ########################################################


#  setup s3 client to bucket that stores user download data
s3 = boto3.client('s3', region_name='us-east-1', aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
                  aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'])

#  create dataframe from weather station metadata
df = download_csv_s3(s3, 'env-can-wx-station-metadata.csv', os.environ['S3_BUCKET'])

#  convert times to datetime format
df[['first_year_hly', 'last_year_hly', 'first_year_dly', 'last_year_dly', 'first_year_mly', 'last_year_mly']] = \
    df[['first_year_hly', 'last_year_hly', 'first_year_dly', 'last_year_dly', 'first_year_mly', 'last_year_mly']].apply(pd.to_datetime, errors='coerce')

#  rename columns
df.columns = ['station_id', 'climate_id', 'province', 'station_name', 'latitude', 'longitude', 'elevation',
              'first_hourly_data', 'last_hourly_data', 'first_daily_data', 'last_daily_data', 'first_monthly_data', 'last_monthly_data']

#  preload loading spinner to base64 encode
spinner = base64.b64encode(open(os.path.join('assets', 'spinner.gif'), 'rb').read())

######################################### PLOTS ########################################################################


#  this function defines the main map of stations
def station_map(stations, lat_selected, lon_selected, name_selected, color):
    return {'data': [
        # weather station locations
        {'type': 'scattermapbox',
         'lat': stations.latitude,
         'lon': stations.longitude,
         'name': '',
         'text': stations.station_name,
         'marker': {'color': color}
         },
        # highlight selected weather station in red
        {'type': 'scattermapbox',
         'lat': [lat_selected],
         'lon': [lon_selected],
         'name': '',
         'text': [name_selected],
         'marker': {'color': 'red'}
         },
    ],
        'layout': {
        'showlegend': False,
        'uirevision': 'static',
        'height': 450,
        'mapbox': {
            'style': 'basic',
            'center': {'lat': 59, 'lon': -97},
            'zoom': 2.5,
            'accesstoken': os.environ['MAPBOX_TOKEN']
        },
        'margin': {'l': 0, 'r': 0, 'b': 0, 't': 0},
        },
    }


######################################### LAYOUT #######################################################################


app_layout = html.Div(
    [
        #  hidden div to store celery background job task-idtask-status, and message-status
        html.Div(id='task-id',
                 children=None,
                 style={'display': 'none'}
                 ),
        #  hidden div to store celery background job task-status
        html.Div(id='task-status',
                 children=None,
                 style={'display': 'none'}
                 ),
        #  hidden div to store status of download message
        html.Div(id='message-status',
                 children=None,
                 style={'display': 'none'}
                 ),
        #  hidden div to store trigger to force table update, this is a "workaround" since Dash Datatable will not
        #  update on row_select
        html.Div(id='false-trigger',
                 children=None,
                 style={'display': 'none'}
                 ),
        #  page refresh interval
        dcc.Interval(
            id='task-refresh-interval',
            interval=24*60*60*1*1000,  # in milliseconds
            n_intervals=0
        ),

        #  header
        html.Div(
            [
                html.Div(
                    [
                        html.H3("Weather History Canada"),
                    ], className='app_header_title',
                ),
                html.Div(
                    [
                        dcc.Link('About', href='/pages/about')
                    ], className='app_header_link',
                ),
            ],
            className='twelve columns app_header',
        ),
        html.Div(
            [
                html.Div(
                    [
                        #  weather station map
                        html.Div(
                            [
                                dcc.Graph(id='station-map',
                                          figure=station_map(df, [], [], [], 'blue'))
                            ], className='graph_style', style={'height': '450px'},
                        ),
                        #  Dash datatable container
                        html.Div(
                            [
                                html.H6('Click on station in map and select in table below prior to generating data', className='filter_box_labels'),
                                dash_table.DataTable(
                                    id='selected-station',
                                    columns=[{"name": col, "id": col} for col in df.columns],
                                    data=[],
                                    style_table={'overflowX': 'scroll'},
                                    style_header={'border': '1px solid black', 'backgroundColor': 'rgb(200, 200, 200)'},
                                    style_cell={'border': '1px solid grey'},
                                    row_selectable='single',
                                ),
                                html.Label('(Multiple stations at the same location may exist)', className='table_subtitle'),
                            ], style={'margin-top': '1rem'},
                        ),
                    ],
                    className='seven columns',
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                #  station name input
                                html.Label("Station Name:", className='filter_box_labels'),
                                html.Div(
                                    [
                                        dcc.Input(
                                            id='station-name',
                                            value='', type='text',
                                            placeholder='Enter Station Name',
                                            className='station_name'),
                                    ],
                                ),
                                #  province input
                                html.Label("Province:", className='filter_box_labels'),
                                html.Div(
                                    [
                                        dcc.Dropdown(
                                            id='province',
                                            options=[{'label': province, 'value': province} for province in df.province.unique()],
                                            style={'width': '90%'}),
                                    ], className='flex_container_row',
                                ),
                                #  data interval input
                                html.Label("Data Interval:", className='filter_box_labels'),
                                html.Div(
                                    [
                                        dcc.Dropdown(
                                            id='frequency',
                                            options=[{'label': frequency, 'value': frequency} for frequency in ['Hourly', 'Daily', 'Monthly']],
                                            style={'width': '90%'}),
                                    ], className='flex_container_row',
                                ),
                                #  date available input
                                html.Label("Data Available Between:", className='filter_box_labels'),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dcc.Dropdown(
                                                    id='first-year',
                                                    options=[{'label': str(year), 'value': str(year)} for year in range(1840, datetime.now().year + 1, 1)],
                                                    placeholder='First Year'),
                                            ], style={'width': '40%'},
                                        ),
                                        html.Div(
                                            [
                                                dcc.Dropdown(
                                                    id='last-year',
                                                    options=[{'label': str(year), 'value': str(year)} for year in range(1840, datetime.now().year + 1, 1)],
                                                    placeholder='Last Year'),
                                            ], style={'width': '40%'},
                                        ),
                                    ], className='flex_container_row',
                                ),
                                #  distance and location input
                                html.Label("Distance Filter:", className='filter_box_labels'),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dcc.Input(
                                                    id='latitude',
                                                    value='', type='text',
                                                    placeholder='Latitude')
                                            ],
                                        ),
                                        html.Div(
                                            [
                                                dcc.Input(
                                                    id='longitude',
                                                    value='',
                                                    type='text',
                                                    placeholder='Longitude')
                                            ],
                                        ),
                                        html.Div(
                                            [
                                                dcc.Dropdown(
                                                    id='radius',
                                                    options=[{'label': radius, 'value': radius} for radius in ['10', '25', '50', '100']],
                                                    placeholder='Kilometers From Location')
                                            ], style={'width': '20%'},
                                        ),
                                    ], className='flex_container_row',
                                ),
                            ], className='filter_box_position',
                        ),
                        html.Div(
                            [
                                #  download dates and message
                                html.Div(
                                    [
                                        html.Label('Download Dates:', className='filter_box_labels'),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        dcc.Dropdown(
                                                            id='download-year-start',
                                                            options=[{'label': year, 'value': year} for year in ['Select A Station']],
                                                            placeholder='Start Year')
                                                    ], style={'width': '40%'},
                                                ),
                                                html.Div(
                                                    [
                                                        dcc.Dropdown(
                                                            id='download-month-start',
                                                            options=[{'label': month, 'value': month} for month in ['Select A Station']],
                                                            placeholder='Start Month')
                                                    ], style={'width': '40%'},
                                                ),
                                            ], className='flex_container_row', style={'margin-bottom': '1rem'},
                                        ),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        dcc.Dropdown(
                                                            id='download-year-end',
                                                            options=[{'label': year, 'value': year} for year in ['Select A Station']],
                                                            placeholder='End Year')
                                                    ], style={'width': '40%'},
                                                ),
                                                html.Div(
                                                    [
                                                        dcc.Dropdown(
                                                            id='download-month-end',
                                                            options=[{'label': month, 'value': month} for month in ['Select A Station']],
                                                            placeholder='End Month')
                                                    ], style={'width': '40%'},
                                                ),
                                            ], className='flex_container_row',
                                        ),
                                        html.Div(
                                            [
                                                html.Label(id='download-message', children='')
                                            ], style={'width': '82%', 'margin-left': '0.5rem'},
                                        ),
                                    ], style={'width': '55%'},
                                ),
                                html.Div(
                                    [
                                        #  download interval and buttons
                                        html.Label('Download Interval:', className='filter_box_labels', style={'margin-left': '3rem'}),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        dcc.Dropdown(
                                                            id='download-frequency',
                                                            options=[{'label': frequency, 'value': frequency} for frequency in ['Select A Station']],
                                                            placeholder='Frequency')
                                                    ], style={'width': '85%'},
                                                ),
                                                html.Div(
                                                    [
                                                        html.A(id='generate-data-button', children='1. GENERATE DATA')
                                                    ], className='data_buttons', style={'border': '2px red dashed','width': '85%'},
                                                ),
                                                html.Div(
                                                    id='toggle-button-vis',
                                                    children=
                                                    [
                                                        html.Div(
                                                            [
                                                                html.A(id='download-data-button', children='2. DOWNLOAD DATA')
                                                            ], className='data_buttons', style={'border': '2px green dashed'},
                                                        ),
                                                        html.Div(
                                                            [
                                                                html.A('3. GRAPH DATA', id='graph-data-button', href="/pages/graph_page")
                                                            ], className='data_buttons', style={'border': '2px blue dashed','margin-top': '1.5rem'},
                                                        ),
                                                    ], style={'display': 'none', 'width': '85%'},
                                                ),
                                                html.Div(
                                                    id='spinner',
                                                    children=
                                                    [
                                                        html.Img(src='data:image/gif;base64,{}'.format(spinner.decode())),
                                                        html.Label(
                                                            id='spinner-label',
                                                            children='Download Progress: Pending....',
                                                            style={'font-weight': 'bold', 'font-size': '16px'}),
                                                    ], style={'display': 'none'},
                                                ),
                                            ], className='flex_container_column',
                                        ),

                                    ], style={'width': '40%'},
                                ),
                            ], className='download_box_position',
                        ),
                    ],
                    className='five columns',
                ),
            ],
            className='row',
        ),
    ],
)


######################################### INTERACTION CALLBACKS ########################################################

# map filter and selected station table callback
@app.callback(
    [Output(component_id='station-map', component_property='figure'),
     Output(component_id='selected-station', component_property='data'),
     Output(component_id='selected-station', component_property='selected_rows'),
     Output(component_id='download-frequency', component_property='value'),
     Output(component_id='download-month-start', component_property='value'),
     Output(component_id='download-month-end', component_property='value'),
     Output(component_id='download-year-start', component_property='value'),
     Output(component_id='download-year-end', component_property='value'),
     Output(component_id='false-trigger', component_property='children')],
    [Input(component_id='province', component_property='value'),
     Input(component_id='frequency', component_property='value'),
     Input(component_id='first-year', component_property='value'),
     Input(component_id='last-year', component_property='value'),
     Input(component_id='latitude', component_property='value'),
     Input(component_id='longitude', component_property='value'),
     Input(component_id='radius', component_property='value'),
     Input(component_id='station-name', component_property='value'),
     Input(component_id='station-map', component_property='clickData')]
)
def data_filter(prov, frequency, first_year, end_year, lat, lon, radius, stn_name, on_map_click):
    #  don't use global variable to filter weather station data on map
    df_filter = df.copy()

    #  filter to limit mapped data by province
    if prov:
        df_filter = df_filter[df_filter.province == prov]
    else:
        df_filter = df_filter

    # filter to limit mapped data by data frequency
    if frequency == 'Hourly':
        df_filter.dropna(subset=['first_hourly_data'], inplace=True)
    elif frequency == 'Daily':
        df_filter.dropna(subset=['first_daily_data'], inplace=True)
    elif frequency == 'Monthly':
        df_filter.dropna(subset=['first_monthly_data'], inplace=True)
    else:
        df_filter = df_filter

    # filter to limit mapped data between specified dates
    if first_year and end_year and frequency == 'Hourly':
        df_filter = df_filter[(df_filter.first_hourly_data <= end_year) & (df_filter.last_hourly_data >= first_year)]
    elif first_year and end_year and frequency == 'Daily':
        df_filter = df_filter[(df_filter.first_daily_data <= end_year) & (df_filter.last_daily_data >= first_year)]
    elif first_year and end_year and frequency == 'Monthly':
        df_filter = df_filter[(df_filter.first_monthly_data <= end_year) & (df_filter.last_monthly_data >= first_year)]
    elif first_year and end_year:
        first_data_record = df_filter[['first_hourly_data', 'first_daily_data', 'first_monthly_data']].min(axis=1)
        last_data_record = df_filter[['last_hourly_data', 'last_daily_data', 'last_monthly_data']].max(axis=1)
        df_filter = df_filter[(first_data_record <= end_year) & (last_data_record >= first_year)]
    else:
        df_filter = df_filter

    # filter to limit mapped data by radius from a specified point
    if lat and lon and radius:
        df_filter = df_filter[
            compute_great_circle_distance(lat, lon, df_filter.latitude, df_filter.longitude) <= np.float64(radius)]
    else:
        df_filter = df_filter

    # filter to limit mapped data by search name
    if stn_name:
        df_filter = df_filter[df_filter.station_name.str.contains(stn_name.upper())]
    else:
        df_filter = df_filter

    # highlight selected station and populate selected station data to a table
    if on_map_click and not df_filter[(df_filter.latitude == on_map_click['points'][0]['lat']) &
                                             (df_filter.longitude == on_map_click['points'][0]['lon'])].empty:
        selected_lat = on_map_click['points'][0]['lat']
        selected_lon = on_map_click['points'][0]['lon']
        selected_station_name = on_map_click['points'][0]['text']

        df_table = df_filter.copy()
        df_table[['first_hourly_data', 'last_hourly_data', 'first_daily_data', 'last_daily_data', 'first_monthly_data', 'last_monthly_data']] = \
            df_table[['first_hourly_data', 'last_hourly_data', 'first_daily_data', 'last_daily_data', 'first_monthly_data', 'last_monthly_data']].apply(lambda x: x.dt.date)

        table_data = df_table[(df_table.latitude == on_map_click['points'][0]['lat']) &
                              (df_table.longitude == on_map_click['points'][0]['lon'])].to_dict('records')
        selected_row = []

    else:
        selected_lat = []
        selected_lon = []
        selected_station_name = []
        table_data = []
        selected_row = []

    return station_map(df_filter, selected_lat, selected_lon, selected_station_name, 'blue'), table_data, selected_row, None, None, None, None, None, None

# download options based on selected station callback
@app.callback(
    [Output(component_id='download-frequency', component_property='options'),
     Output(component_id='download-month-start', component_property='options'),
     Output(component_id='download-month-end', component_property='options'),
     Output(component_id='download-year-start', component_property='options'),
     Output(component_id='download-year-end', component_property='options')],
    [Input(component_id='selected-station', component_property='data'),
     Input(component_id='selected-station', component_property='selected_rows'),
     Input(component_id='download-frequency', component_property='value'),
     Input(component_id='false-trigger', component_property='children')]
)
def update_download_dropdowns(selected_station, selected_station_row, selected_frequency, false_trigger):

    if selected_station and selected_station_row:

        #  attributes of selected station
        df_selected_data = pd.DataFrame(selected_station).iloc[selected_station_row[0]]

        #  populate dropdown tab with available frequency of data to download
        available_frequency = df_selected_data[['first_hourly_data', 'first_daily_data', 'first_monthly_data']].dropna().index.to_list()
        download_frequency = [{'label': freq.split('_')[1].capitalize(), 'value': freq.split('_')[1].capitalize()} for freq in available_frequency]

        if not any(freq['value'] == selected_frequency for freq in download_frequency):
            selected_frequency = None

        #  populate dropdown tab with available months of data to download
        download_month_start = [{'label': year, 'value': year} for year in range(1, 13, 1)]
        download_month_end = download_month_start  # same month range for downloads

        #  populate dropdown tab with available years of data to download corresponding to map filter data frequency
        if selected_frequency == 'Hourly':
            download_year_start = [{'label': year, 'value': year} for year in
                                   set(pd.date_range(df_selected_data.first_hourly_data, df_selected_data.last_hourly_data, freq='MS').year)]
            download_year_end = download_year_start  # same year range for downloads
        elif selected_frequency == 'Daily':
            download_year_start = [{'label': year, 'value': year} for year in
                                   set(pd.date_range(df_selected_data.first_daily_data, df_selected_data.last_daily_data, freq='MS').year)]
            download_year_end = download_year_start  # same year range for downloads
        elif selected_frequency == 'Monthly':
            download_year_start = [{'label': year, 'value': year} for year in
                                   set(pd.date_range(df_selected_data.first_monthly_data, df_selected_data.last_monthly_data, freq='MS').year)]
            download_year_end = download_year_start  # same year range for downloads
        else:
            first_data_record = pd.to_datetime(df_selected_data[['first_hourly_data', 'first_daily_data', 'first_monthly_data']]).min().year
            last_data_record = pd.to_datetime(df_selected_data[['last_hourly_data', 'last_daily_data', 'last_monthly_data']]).max().year
            download_year_start = [{'label': year, 'value': year} for year in
                                   set(pd.date_range(str(first_data_record), str(last_data_record), freq='MS').year)]
            download_year_end = download_year_start  # same year range for downloads

    else:
        no_station_selected = [{'label': year, 'value': year} for year in ['Select A Station']]
        download_frequency, download_month_start, download_month_end, download_year_start, download_year_end = \
            no_station_selected, no_station_selected, no_station_selected, no_station_selected, no_station_selected

    return download_frequency, download_month_start, download_month_end, download_year_start, download_year_end

# download message based on download settings selected callback
@app.callback(
    [Output(component_id='download-message', component_property='children'),
     Output(component_id='download-message', component_property='style'),
     Output(component_id='message-status', component_property='children')],
    [Input(component_id='selected-station', component_property='data'),
     Input(component_id='download-year-start', component_property='value'),
     Input(component_id='download-year-end', component_property='value'),
     Input(component_id='download-month-start', component_property='value'),
     Input(component_id='download-month-end', component_property='value'),
     Input(component_id='download-frequency', component_property='value'),
     Input(component_id='selected-station', component_property='selected_rows'),
     Input(component_id='false-trigger', component_property='children')]
)
def update_download_message(selected_station, download_start_year, download_end_year, download_start_month, download_end_month, selected_frequency, selected_station_row, false_trigger):

    # if all the necessary download settings have been selected then display download message
    if selected_station and selected_frequency and download_start_year and download_start_month and download_end_year and download_end_month:

        #  dataframe of tabledata
        df_selected_data = pd.DataFrame(selected_station).iloc[selected_station_row[0]]

        #  hack to reset message status if frequency is not in available frequncy, otherwise error in download
        available_frequency = df_selected_data[['first_hourly_data', 'first_daily_data', 'first_monthly_data']].dropna().index.to_list()
        download_frequency = [freq.split('_')[1].capitalize() for freq in available_frequency]

        #  if the same start and end data are chose advise user to select something else
        if download_start_year == download_end_year and download_start_month == download_end_month:
            message = 'Download dates must be different'
            message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem', 'border': '2px red dashed'}
            message_status = None

        # if the start year is after the end year advise the user to select something else
        elif download_start_year > download_end_year:
            message = 'Download start date must preceed download end date'
            message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem', 'border': '2px red dashed'}
            message_status = None

        # if the start date is after the end date advise the user to select something else
        elif download_start_year == download_end_year and download_start_month > download_end_month:
            message = 'Download start date must preceed download end date'
            message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem', 'border': '2px red dashed'}
            message_status = None

        # if no station is selected remove message
        elif not selected_station_row:
            message = []
            message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem'}
            message_status = None

        elif selected_frequency not in download_frequency:
            message = []
            message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem'}
            message_status = None

        # if all the options are correct and present then provide the download message
        else:
            start_date = datetime.strptime(str(download_start_year) + str(download_start_month) + '1', '%Y%m%d').date()
            end_date = datetime.strptime(str(download_end_year) + str(download_end_month) + '1', '%Y%m%d').date() - timedelta(1)
            message = 'First select GENERATE DATA and once loading is complete select DOWNLOAD DATA to begin downloading {} ' \
                      'data from {} to {} for station {} (station ID {})' \
                    .format(selected_frequency, start_date, end_date, df_selected_data.station_name, df_selected_data.station_id)
            message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem', 'border': '2px red dashed'}
            message_status = 'PROCEED'

    else:
        message = []
        message_style = {'width': '100%', 'margin-right': '1rem', 'margin-top': '1rem'}
        message_status = None

    return message, message_style, message_status

# Send download to Celery background worker on Heroku and link to download button
@app.callback(
    [Output(component_id='download-data-button', component_property='href'),
     Output(component_id='task-id', component_property='children'),
     Output(component_id='filename-store', component_property='data'),
     Output(component_id='station-metadata-store', component_property='data'),
     Output(component_id='task-status', component_property='children'),
     Output(component_id='task-refresh-interval', component_property='interval'),
     Output(component_id='toggle-button-vis', component_property='style'),
     Output(component_id='spinner', component_property='style'),
     Output(component_id='variable-name-store', component_property='data'),
     Output(component_id='spinner-label', component_property='children')],
    [Input(component_id='selected-station', component_property='data'),
     Input(component_id='download-year-start', component_property='value'),
     Input(component_id='download-year-end', component_property='value'),
     Input(component_id='download-month-start', component_property='value'),
     Input(component_id='download-month-end', component_property='value'),
     Input(component_id='download-frequency', component_property='value'),
     Input(component_id='generate-data-button', component_property='n_clicks'),
     Input(component_id='message-status', component_property='children'),
     Input(component_id='task-refresh-interval', component_property='n_intervals'),
     Input(component_id='selected-station', component_property='selected_rows')],
    [State(component_id='task-status', component_property='children'),
     State(component_id='task-id', component_property='children')]
)
def background_download_task(selected_station, download_start_year, download_end_year, download_start_month,
                             download_end_month, download_frequency, generate_button_click, message_status,
                             n_int, selected_station_row, task_status_state, task_id_state):

    #  look for specific click event
    ctx = dash.callback_context

    #  if the user has set the download timeframe, frequency, and no current task is running then launch celery
    if ctx.triggered[0]['prop_id'] == 'generate-data-button.n_clicks' and generate_button_click and message_status == 'PROCEED' and task_status_state is None:

        #  create data frame of selected station metadata
        df_selected_data = pd.DataFrame(selected_station).iloc[selected_station_row[0]]
        station_metadata = {k: v for v, k in enumerate([df_selected_data.latitude, df_selected_data.longitude, df_selected_data.station_name])}

        #  create filename link for S3 download following background task
        if download_frequency == 'Hourly':
            output_filename = '_'.join(['WHC', df_selected_data.station_name.replace(' ', '_'), str(df_selected_data.station_id),
                                 str(download_start_year), str(download_end_year), 'hourly.csv'])
        elif download_frequency == 'Daily':
            output_filename = '_'.join(['WHC', df_selected_data.station_name.replace(' ', '_'), str(df_selected_data.station_id),
                                 str(download_start_year), str(download_end_year), 'daily.csv'])
        elif download_frequency == 'Monthly':
            output_filename = '_'.join(['WHC', df_selected_data.station_name.replace(' ', '_'), str(df_selected_data.station_id),
                                 str(download_start_year), str(download_end_year), 'monthly.csv'])

        relative_filename = os.path.join('download', output_filename)
        link_path = '/{}'.format(relative_filename)

        #  start background task in Celery and Redis
        download_task = tasks.download_remote_data.apply_async([df_selected_data.station_name, output_filename, str(df_selected_data.station_id), str(download_start_year),
                                                                str(download_start_month), str(download_end_year), str(download_end_month), download_frequency])

        #  task id of current celery task
        task_id = download_task.id
        time.sleep(0.5)  # Need a short sleep for task_id to catch up

        task = AsyncResult(id=task_id, app=celery_app)
        current_task_status = task.state
        current_task_progress = 'Download Starting...'
        interval = 500  # set refresh interval short and to update task status
        loading_div_viz = {'display': 'inline-block', 'text-align': 'center'}
        button_visibility = {'display': 'none'}

        return link_path, task_id, output_filename, station_metadata, current_task_status, interval, button_visibility, loading_div_viz, dash.no_update, current_task_progress

    #  task will be pending if it's waiting in the queue
    elif task_status_state == 'PENDING':
        task = AsyncResult(id=task_id_state, app=celery_app)
        current_task_status = task.state
        current_task_progress = 'Download Pending...'

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, current_task_status, dash.no_update, dash.no_update, dash.no_update, dash.no_update, current_task_progress

    #  task will be in progress if a worker has accepted it
    elif task_status_state == 'PROGRESS':
        task = AsyncResult(id=task_id_state, app=celery_app)
        current_task_status = task.state
        current_task_progress = 'Downloading...May Take A Few Minutes'

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, current_task_status, dash.no_update, dash.no_update, dash.no_update, dash.no_update, current_task_progress

    #  task will be successful once the worker releases it. DOES NOT MEAN REDIS HAS RESULTS YET
    elif task_status_state == 'SUCCESS':
        task = AsyncResult(id=task_id_state, app=celery_app)
        current_task_status = task.state
        current_task_progress = 'Download Complete!!!'
        interval = 500
        loading_div_viz = {'display': 'inline-block', 'text-align': 'center'}
        button_visibility = {'display': 'none'}
        task_result = {}

        #  just because status is SUCCESS doesnt mean the results made it to redis, need to wait for redis results
        if 'result' in task.info:
            current_task_status = None
            interval = 24*60*60*1*1000
            loading_div_viz = {'display': 'none'}
            button_visibility = {'display': 'block'}
            task_result = task.info
            task_result.pop('result', None)  # remove key
            task.forget()

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, current_task_status, interval, button_visibility, loading_div_viz, task_result, current_task_progress

    #  task will fail if celery indicates an error
    elif task_status_state == 'FAILURE':
        task = AsyncResult(id=task_id_state, app=celery_app)
        current_task_progress = 'Download Failed. Please refresh page and try again.'
        interval = 24 * 60 * 60 * 1 * 1000
        task.forget()

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, interval, dash.no_update, dash.no_update, dash.no_update, current_task_progress

    elif message_status is None:
        button_visibility = {'display': 'none'}

        return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update, button_visibility, dash.no_update, dash.no_update, dash.no_update

    else:
        raise dash.exceptions.PreventUpdate

#  flask route for file download
@app.server.route('/download/<filename>')
def serve_static(filename):

    #  presigned url for user to download file directly from s3, removes storage from memory
    url = s3.generate_presigned_url('get_object', Params={'Bucket': os.environ['S3_BUCKET'], 'Key': 'tmp/' + filename}, ExpiresIn=100)

    return redirect(url, code=302)