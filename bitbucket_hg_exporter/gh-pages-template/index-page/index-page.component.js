'use strict';

// Register `indexPage` component, along with its associated controller and template
angular.
  module('indexPage').
  component('indexPage', {
    templateUrl: 'index-page/index-page.template.html',
    controller: ['$http', '$routeParams', '$scope', '$rootScope', function IndexPageController($http, $routeParams, $scope, $routeScope) {
        var self = this;
        self.scope = $scope;
        console.log($routeScope.project_data)
        
    }]
  });